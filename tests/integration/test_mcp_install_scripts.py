"""安装/卸载脚本只在 pytest 临时目录中验证。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import json
import tomllib
from pathlib import Path

import pytest


def _powershell() -> str:
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("当前主机没有 PowerShell")
    return executable


def _package(tmp_path: Path, *, version: str = "1.0.0", executable_bytes: bytes = b"offline-test-executable") -> Path:
    package = tmp_path / "package"
    package.mkdir(parents=True)
    executable = package / "davinci-gw-mcp.exe"
    executable.write_bytes(executable_bytes)
    internal = package / "_internal"
    internal.mkdir()
    runtime = internal / "runtime.dat"
    runtime.write_bytes(b"offline-runtime")
    version_file = package / "VERSION"
    version_file.write_text(f"{version}\n", encoding="utf-8")
    lines = [
        f"{hashlib.sha256(executable.read_bytes()).hexdigest()}  davinci-gw-mcp.exe",
        f"{hashlib.sha256(runtime.read_bytes()).hexdigest()}  _internal/runtime.dat",
        f"{hashlib.sha256(version_file.read_bytes()).hexdigest()}  VERSION",
    ]
    (package / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    return package


def _run(script: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), *arguments],
        check=check, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )


def test_install_is_checksum_guarded_idempotent_and_uninstall_preserves_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    install_root = tmp_path / "installed"
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('model = "gpt-5.6"\n', encoding="utf-8")
    install_script = project / "scripts" / "install_mcp.ps1"
    uninstall_script = project / "scripts" / "uninstall_mcp.ps1"
    common = [
        "-PackageRoot", str(package), "-InstallRoot", str(install_root), "-CodexHome", str(codex_home),
    ]

    _run(install_script, *common)
    first_config = config.read_text(encoding="utf-8-sig")
    runtime_root = install_root / "versions" / "1.0.0"
    assert (runtime_root / "davinci-gw-mcp.exe").read_bytes() == b"offline-test-executable"
    assert (runtime_root / "_internal" / "runtime.dat").read_bytes() == b"offline-runtime"
    assert (install_root / "install-manifest.json").is_file()
    assert first_config.count("# BEGIN DAVINCI_GW_MCP MANAGED BLOCK") == 1
    assert 'model = "gpt-5.6"' in first_config
    assert 'approval_mode = "prompt"' in first_config

    _run(install_script, *common)
    assert config.read_text(encoding="utf-8-sig") == first_config
    assert config.read_text(encoding="utf-8-sig").count("[mcp_servers.davinci_gateway]") == 1

    _run(
        uninstall_script,
        "-InstallRoot", str(install_root), "-CodexHome", str(codex_home),
    )
    final_config = config.read_text(encoding="utf-8-sig")
    assert 'model = "gpt-5.6"' in final_config
    assert "DAVINCI_GW_MCP" not in final_config
    assert "mcp_servers.davinci_gateway" not in final_config
    assert not runtime_root.exists()
    assert not (install_root / "install-manifest.json").exists()
    assert list(codex_home.glob("config.toml.*.bak"))
    # 已完整卸载时再次执行不猜测路径，也不会报错。
    _run(
        uninstall_script,
        "-InstallRoot", str(install_root), "-CodexHome", str(codex_home),
    )


def test_install_rejects_tampered_executable(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    (package / "davinci-gw-mcp.exe").write_bytes(b"tampered")
    install_root = tmp_path / "rejected"
    result = _run(
        project / "scripts" / "install_mcp.ps1",
        "-PackageRoot", str(package), "-InstallRoot", str(install_root),
        "-CodexHome", str(tmp_path / "codex-home"),
        check=False,
    )
    assert result.returncode != 0
    assert not (install_root / "versions" / "1.0.0" / "davinci-gw-mcp.exe").exists()


def test_install_rejects_unlisted_internal_file(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    (package / "_internal" / "unlisted.dat").write_bytes(b"not-in-checksum")
    install_root = tmp_path / "rejected"
    result = _run(
        project / "scripts" / "install_mcp.ps1",
        "-PackageRoot", str(package), "-InstallRoot", str(install_root),
        "-CodexHome", str(tmp_path / "codex-home"), check=False,
    )
    assert result.returncode != 0
    assert not (install_root / "versions" / "1.0.0").exists()


def test_install_preflights_unmanaged_codex_block_before_copy(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    install_root = tmp_path / "must-stay-empty"
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    original = '[mcp_servers.davinci_gateway]\ncommand = "owner-managed.exe"\n'
    config.write_text(original, encoding="utf-8")
    result = _run(
        project / "scripts" / "install_mcp.ps1",
        "-PackageRoot", str(package), "-InstallRoot", str(install_root),
        "-CodexHome", str(codex_home), check=False,
    )
    assert result.returncode != 0
    assert not (install_root / "versions" / "1.0.0" / "davinci-gw-mcp.exe").exists()
    assert config.read_text(encoding="utf-8-sig") == original


def test_uninstall_refuses_tampered_executable_before_changing_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    install_root = tmp_path / "installed"
    codex_home = tmp_path / "codex-home"
    _run(
        project / "scripts" / "install_mcp.ps1",
        "-PackageRoot", str(package), "-InstallRoot", str(install_root),
        "-CodexHome", str(codex_home),
    )
    target = install_root / "versions" / "1.0.0" / "davinci-gw-mcp.exe"
    target.write_bytes(b"changed-after-install")
    config = codex_home / "config.toml"
    before = config.read_text(encoding="utf-8-sig")
    result = _run(
        project / "scripts" / "uninstall_mcp.ps1",
        "-InstallRoot", str(install_root), "-CodexHome", str(codex_home), check=False,
    )
    assert result.returncode != 0
    assert target.is_file()
    assert config.read_text(encoding="utf-8-sig") == before


def test_new_version_installs_beside_running_old_version_and_switches_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    first = _package(tmp_path / "first")
    second = _package(tmp_path / "second", version="1.0.1", executable_bytes=b"new-version")
    install_root = tmp_path / "installed"
    codex_home = tmp_path / "codex-home"
    script = project / "scripts" / "install_mcp.ps1"
    for package in (first, second):
        _run(
            script, "-PackageRoot", str(package), "-InstallRoot", str(install_root),
            "-CodexHome", str(codex_home),
        )
    assert (install_root / "versions" / "1.0.0" / "davinci-gw-mcp.exe").is_file()
    assert (install_root / "versions" / "1.0.1" / "davinci-gw-mcp.exe").read_bytes() == b"new-version"
    config = (codex_home / "config.toml").read_text(encoding="utf-8-sig")
    assert "versions\\\\1.0.1\\\\davinci-gw-mcp.exe" in config
    manifest = (install_root / "install-manifest.json").read_text(encoding="utf-8-sig")
    assert manifest.count('"runtime_directory"') == 2

    _run(
        project / "scripts" / "uninstall_mcp.ps1",
        "-InstallRoot", str(install_root), "-CodexHome", str(codex_home),
    )
    assert not (install_root / "versions" / "1.0.0").exists()
    assert not (install_root / "versions" / "1.0.1").exists()


@pytest.mark.parametrize("original", ["[]\n", "# retained comment\n[]\n",
                         "- id: unrelated-plugin\n  disabled: true\n",
                         "- id: unrelated-plugin\n  config:\n    text: |+\n      keep\n\n\n"])
def test_dsh_install_preserves_patch_and_repeated_install_is_idempotent(tmp_path: Path, original: str) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    dsh_home = tmp_path / "dsh-home"
    dsh_home.mkdir()
    config = dsh_home / "cordis.patch.yml"
    config.write_text(original, encoding="utf-8")
    install_root = tmp_path / "installed"
    args = ("-PackageRoot", str(package), "-InstallRoot", str(install_root),
            "-Client", "DeepSeekHarness", "-DshHome", str(dsh_home))
    script = project / "scripts" / "install_mcp.ps1"
    _run(script, *args)
    first = config.read_text(encoding="utf-8")
    assert "name: mcp-client" in first and "serverName: davinci_gateway" in first
    assert "toolCallTimeoutMs: 3600000" in first
    if original.startswith('-'):
        assert first.startswith(original)
    elif original.startswith('#'):
        assert original.splitlines()[0] in first
    _run(script, *args)
    assert config.read_text(encoding="utf-8") == first
    assert len(list(dsh_home.glob("cordis.patch.yml.*.bak"))) == 1
    _run(project / "scripts" / "uninstall_mcp.ps1", "-InstallRoot", str(install_root),
         "-Client", "DeepSeekHarness", "-DshHome", str(dsh_home))
    final = config.read_text(encoding="utf-8")
    assert "davinci_gateway" not in final
    if original.startswith('-'):
        assert final == original
    elif original.startswith('#'):
        assert original.splitlines()[0] in final
    assert not (install_root / "versions" / "1.0.0").exists()


def test_dsh_unmanaged_server_blocks_install_before_copy(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    home = tmp_path / "dsh"
    home.mkdir()
    original = '- insert:\n    - name: mcp-client\n      config:\n        serverName: davinci_gateway\n'
    (home / "cordis.patch.yml").write_text(original, encoding="utf-8")
    install_root = tmp_path / "installed"
    result = _run(project / "scripts" / "install_mcp.ps1", "-PackageRoot", str(package),
                  "-InstallRoot", str(install_root), "-Client", "DeepSeekHarness",
                  "-DshHome", str(home), check=False)
    assert result.returncode != 0
    assert not install_root.exists()
    assert (home / "cordis.patch.yml").read_text(encoding="utf-8") == original


def test_setup_check_does_not_install_or_write_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    install_root, home = tmp_path / "installed", tmp_path / "dsh"
    result = _run(project / "scripts" / "setup.ps1", "-Client", "DeepSeekHarness",
                  "-DshHome", str(home), "-InstallRoot", str(install_root), "-Check")
    planned = json.loads(result.stdout)
    assert planned["Client"] == "DeepSeekHarness"
    assert planned["ConfigPath"] == str(home / "cordis.patch.yml")
    assert not install_root.exists() and not home.exists()


def test_source_client_config_preserves_python_arguments(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    command = tmp_path / "Python with spaces" / "python.exe"
    command.parent.mkdir()
    command.write_bytes(b"placeholder")
    home = tmp_path / "codex"
    expression = (
        f"& '{project / 'scripts' / 'configure_mcp_client.ps1'}' -Client Codex "
        f"-Command '{command}' -Arguments @('-m','davinci_gw.mcp') -CodexHome '{home}'"
    )
    subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", expression],
                   check=True, capture_output=True, timeout=30)
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    server = config["mcp_servers"]["davinci_gateway"]
    assert server["command"] == str(command)
    assert server["args"] == ["-m", "davinci_gw.mcp"]


def test_setup_rejects_unlaunchable_package_before_client_configuration(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    package = _package(tmp_path, version=version)
    install_root, home = tmp_path / "installed", tmp_path / "dsh"
    result = _run(project / "scripts" / "setup.ps1", "-Client", "DeepSeekHarness",
                  "-DshHome", str(home), "-InstallRoot", str(install_root),
                  "-PackageRoot", str(package), check=False)
    assert result.returncode != 0
    assert not install_root.exists() and not home.exists()


def test_packaged_setup_beneath_source_root_uses_its_own_version_and_runtime(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    package = _package(tmp_path)
    shutil.copyfile(project / "scripts" / "setup.ps1", package / "setup.ps1")
    result = _run(package / "setup.ps1", "-Client", "None", "-Check")
    planned = json.loads(result.stdout)
    assert planned["Version"] == "1.0.0"
    assert planned["PackageRoot"] == str(package)
    assert not planned["DownloadRequired"]


def test_setup_resolves_relative_package_from_powershell_location(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    package = _package(tmp_path)
    expression = (f"Set-Location -LiteralPath '{tmp_path}'; "
                  f"& '{project / 'scripts' / 'setup.ps1'}' -Client None -PackageRoot 'package' -Check")
    result = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", expression],
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert json.loads(result.stdout)["PackageRoot"] == str(package)


def _mirror_checkout(tmp_path: Path) -> Path:
    project = Path(__file__).resolve().parents[2]
    checkout = tmp_path / "mirror-checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nversion = "1.1.5"\n', encoding="utf-8")
    shutil.copytree(project / "scripts", checkout / "scripts")
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin",
                    "http://192.168.8.160/hao.xin/davinci-gw-mirror.git"], check=True, capture_output=True)
    return checkout


@pytest.mark.parametrize("remote", ["origin", "company"])
def test_mirror_auto_chooses_local_build_and_requires_environment_choice(tmp_path: Path, remote: str) -> None:
    checkout = _mirror_checkout(tmp_path)
    if remote != "origin":
        subprocess.run(["git", "-C", str(checkout), "remote", "rename", "origin", remote], check=True)
    install_root = tmp_path / "installed"
    result = _run(checkout / "scripts/setup.ps1", "-Client", "None",
                  "-InstallRoot", str(install_root), "-Check")
    planned = json.loads(result.stdout)
    assert planned["Mode"] == "Build"
    assert not planned["DownloadRequired"]
    assert planned["NeedsEnvironmentChoice"]
    result = _run(checkout / "scripts/setup.ps1", "-Client", "None",
                  "-InstallRoot", str(install_root), check=False)
    assert result.returncode != 0
    assert "ENVIRONMENT_CHOICE_REQUIRED" in result.stderr
    assert not install_root.exists() and not (checkout / ".venv").exists()


@pytest.mark.parametrize("environment", ["Direct", "Miniconda"])
def test_mirror_environment_plan_needs_no_existing_python(tmp_path: Path, environment: str) -> None:
    checkout = _mirror_checkout(tmp_path)
    runtime = tmp_path / "environment"
    result = _run(checkout / "scripts/setup.ps1", "-Client", "None", "-Mode", "Source",
                  "-Environment", environment, "-RuntimeRoot", str(runtime), "-Check")
    planned = json.loads(result.stdout)
    expected = (checkout / ".venv/Scripts/python.exe" if environment == "Direct"
                else runtime / "Miniconda3/envs/davinci-gw/python.exe")
    assert planned["Command"] == str(expected)
    assert not planned["DownloadRequired"] and not planned["NeedsEnvironmentChoice"]
    assert not runtime.exists()


def test_explicit_build_reuses_existing_local_package(tmp_path: Path) -> None:
    checkout = _mirror_checkout(tmp_path)
    package = checkout / "release/davinci-gw-mcp-1.1.5-win-x64"
    package.mkdir(parents=True)
    (package / "davinci-gw-mcp.exe").write_bytes(b"test")
    result = _run(checkout / "scripts/setup.ps1", "-Client", "None", "-Mode", "Build", "-Check")
    planned = json.loads(result.stdout)
    assert planned["Mode"] == "Package" and planned["PackageRoot"] == str(package)
    assert not planned["NeedsEnvironmentChoice"] and not planned["DownloadRequired"]
