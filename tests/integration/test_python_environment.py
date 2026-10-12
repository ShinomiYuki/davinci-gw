"""验证国内镜像切换及用户配置隔离，不安装系统环境。"""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def _run(expression: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("需要 PowerShell 7")
    return subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", expression],
                          capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_installer_retries_network_and_signature_failures(tmp_path: Path) -> None:
    module = Path(__file__).resolve().parents[2] / "scripts/python_environment.psm1"
    destination = tmp_path / "installer.exe"
    result = _run(f"""
$module = Import-Module {_quote(module)} -Force -DisableNameChecking -PassThru
& $module {{
    $script:attempts = [Collections.Generic.List[string]]::new()
    function Invoke-WebRequest {{
        param($Uri,$Method,$TimeoutSec,$OutFile)
        if ($Method -eq 'Head') {{
            $script:attempts.Add($Uri)
            if ($Uri -like '*unavailable*') {{ throw 'unreachable' }}
        }} else {{ [IO.File]::WriteAllText($OutFile,$Uri) }}
    }}
    function Get-AuthenticodeSignature {{
        param($LiteralPath)
        $content = [IO.File]::ReadAllText($LiteralPath)
        [pscustomobject]@{{ Status=if ($content -like '*unsigned*') {{ 'NotSigned' }} else {{ 'Valid' }};
            SignerCertificate=[pscustomobject]@{{ Subject='CN=Python Software Foundation' }} }}
    }}
    Save-GatewayInstaller @('https://unavailable.example/a','https://unsigned.example/a','https://valid.example/a') {_quote(destination)} 'Python Software Foundation'
    $script:attempts | ConvertTo-Json -Compress
}} 3>$null 6>$null
""")
    assert json.loads(result.stdout) == ["https://unavailable.example/a", "https://unsigned.example/a",
                                       "https://valid.example/a"]
    assert destination.read_text() == "https://valid.example/a"


def test_pip_fallback_ignores_external_sources_and_restores_environment(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    fake_python = tmp_path / "python.ps1"
    record = tmp_path / "attempts.jsonl"
    fake_python.write_text("""
if ($args[0] -eq '-c') {
    '{"proxy":"http://company-proxy:8080","cert":"C:/company/ca.pem"}'
    $global:LASTEXITCODE = 0
    return
}
$args | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:GATEWAY_TEST_RECORD
if ($env:PIP_EXTRA_INDEX_URL -or $env:PIP_FIND_LINKS -or $env:PIP_CONFIG_FILE -ne 'nul') { throw 'external configuration leaked' }
$global:LASTEXITCODE = if ($args -contains 'https://unavailable.example/simple') { 1 } else { 0 }
""", encoding="utf-8")
    result = _run(f"""
$module = Import-Module {_quote(project / 'scripts/python_environment.psm1')} -Force -DisableNameChecking -PassThru
$env:GATEWAY_TEST_RECORD = {_quote(record)}
$env:PIP_EXTRA_INDEX_URL = 'https://github.com/unwanted'
$env:PIP_FIND_LINKS = 'https://github.com/unwanted-files'
$env:PIP_INDEX_URL = 'https://github.com/unwanted-index'
$env:PIP_CONFIG_FILE = 'original.ini'
& $module {{
    function Get-GatewayMirrors {{ [pscustomobject]@{{pip=@('https://unavailable.example/simple','https://valid.example/simple')}} }}
    Install-GatewayDependencies {_quote(fake_python)} {_quote(project)} -Build
}} 3>$null
[ordered]@{{ Extra=$env:PIP_EXTRA_INDEX_URL; Links=$env:PIP_FIND_LINKS; Config=$env:PIP_CONFIG_FILE; Index=$env:PIP_INDEX_URL }} | ConvertTo-Json -Compress
""")
    attempts = [json.loads(line) for line in record.read_text(encoding="utf-8-sig").splitlines()]
    assert len(attempts) == 2
    assert attempts[0][attempts[0].index("--index-url") + 1] == "https://unavailable.example/simple"
    assert attempts[1][attempts[1].index("--index-url") + 1] == "https://valid.example/simple"
    assert str(project) + "[mcp]" in attempts[1] and "pyinstaller>=6.15,<7" in attempts[1]
    assert attempts[1][attempts[1].index("--proxy") + 1] == "http://company-proxy:8080"
    assert attempts[1][attempts[1].index("--cert") + 1] == "C:/company/ca.pem"
    assert json.loads(result.stdout) == {"Extra": "https://github.com/unwanted",
                                        "Links": "https://github.com/unwanted-files", "Config": "original.ini",
                                        "Index": "https://github.com/unwanted-index"}


def test_pip_connection_reader_preserves_effective_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    config = tmp_path / "pip.ini"
    config.write_text("[global]\nproxy = http://company-proxy:8080\ncert = global.pem\n"
                      "[install]\ncert = install.pem\n", encoding="utf-8")
    fake_python = tmp_path / "python.ps1"
    record = tmp_path / "arguments.json"
    fake_python.write_text(f"""
if ($args[0] -eq '-c') {{
    & {_quote(sys.executable)} -c $args[1]
    return
}}
$args | ConvertTo-Json -Compress | Set-Content -LiteralPath {_quote(record)}
$global:LASTEXITCODE = 0
""", encoding="utf-8")
    _run(f"""
Import-Module {_quote(project / 'scripts/python_environment.psm1')} -Force -DisableNameChecking
$env:PIP_CONFIG_FILE = {_quote(config)}
Install-GatewayDependencies {_quote(fake_python)} {_quote(project)} -PipIndexUrl 'https://internal.example/simple'
""")
    arguments = json.loads(record.read_text(encoding="utf-8-sig"))
    assert arguments[arguments.index("--proxy") + 1] == "http://company-proxy:8080"
    assert arguments[arguments.index("--cert") + 1] == "install.pem"
