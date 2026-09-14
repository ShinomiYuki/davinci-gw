"""安装/卸载脚本只在 pytest 临时目录中验证。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
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
