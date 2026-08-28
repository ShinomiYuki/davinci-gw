"""对实际 PyInstaller 单文件 EXE 执行协议、无 Python 环境和退出验证。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _executable() -> Path:
    configured = os.environ.get("DAVINCI_GW_MCP_EXE")
    if not configured:
        pytest.skip("仅在发布验证中设置 DAVINCI_GW_MCP_EXE")
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.fail(f"MCP EXE 不存在：{path}")
    return path


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "PATH": str(Path(system_root) / "System32"),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path / "local-app-data"),
        "DAVINCI_GW_LOG_DIR": str(tmp_path / "logs"),
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "socks5://127.0.0.1:1",
        "NO_PROXY": "",
    }


@pytest.mark.slow
def test_exe_runs_full_stdio_flow_without_python_or_source_paths(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    executable = _executable()
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    output = tmp_path / "exe_generated.arxml"
    parameters = StdioServerParameters(
        command=str(executable), args=[], cwd=tmp_path, env=_isolated_environment(tmp_path),
    )

    async def scenario() -> None:
        with (tmp_path / "exe-stderr.log").open("w", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "davinci-gw-mcp"
                    listed = await session.list_tools()
                    assert len(listed.tools) == 4
                    validated = await session.call_tool("validate_gateway_inputs", {
                        "config_path": str(config.resolve()), "baseline_path": str(baseline.resolve()),
                    }, read_timeout_seconds=60)
                    assert validated.structured_content["status"] == "SUCCESS"
                    preview = await session.call_tool("preview_gateway_update", {
                        "config_path": str(config.resolve()), "baseline_path": str(baseline.resolve()),
                    }, read_timeout_seconds=60)
                    assert preview.structured_content["status"] == "SUCCESS"
                    generated = await session.call_tool("generate_gateway_arxml", {
                        "preparation_id": preview.structured_content["preparation_id"],
                        "output_path": str(output.resolve()),
                    }, read_timeout_seconds=60)
                    assert generated.structured_content["status"] == "SUCCESS"
                    assert output.is_file()

    asyncio.run(scenario())


@pytest.mark.slow
def test_exe_has_no_tcp_connections_and_exits_cleanly_on_eof(tmp_path: Path) -> None:
    executable = _executable()
    process = subprocess.Popen(
        [str(executable)], cwd=tmp_path, env=_isolated_environment(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(3)
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            query = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command",
                 f"@(Get-NetTCPConnection -OwningProcess {process.pid} -ErrorAction SilentlyContinue).Count"],
                check=True, capture_output=True, text=True, timeout=15,
            )
            assert query.stdout.strip() == "0"
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=30) == 0
        assert process.stdout is not None
        assert process.stdout.read() == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
