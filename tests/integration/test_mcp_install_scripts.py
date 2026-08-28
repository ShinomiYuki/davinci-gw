"""安装/卸载脚本只在 pytest 临时目录中验证。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("当前主机没有 PowerShell")
    return executable


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    package.mkdir()
    executable = package / "davinci-gw-mcp.exe"
    executable.write_bytes(b"offline-test-executable")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (package / "SHA256SUMS.txt").write_text(f"{digest}  davinci-gw-mcp.exe\n", encoding="ascii")
    (package / "VERSION").write_text("0.2.0\n", encoding="utf-8")
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
    assert (install_root / "davinci-gw-mcp.exe").read_bytes() == b"offline-test-executable"
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
    assert not (install_root / "davinci-gw-mcp.exe").exists()
    assert not (install_root / "install-manifest.json").exists()
    assert list(codex_home.glob("config.toml.*.bak"))


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
    assert not (install_root / "davinci-gw-mcp.exe").exists()


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
    assert not (install_root / "davinci-gw-mcp.exe").exists()
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
    target = install_root / "davinci-gw-mcp.exe"
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
