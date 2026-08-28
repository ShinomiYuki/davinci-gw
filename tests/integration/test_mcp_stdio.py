"""使用官方 MCP 客户端驱动真实子进程 STDIO 协议。"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_protocol_lists_tools_and_completes_prepare_generate(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory()  # type: ignore[operator]
    baseline = arxml_factory()  # type: ignore[operator]
    output = tmp_path / "stdio_generated.arxml"
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    environment["DAVINCI_GW_LOG_DIR"] = str(tmp_path / "logs")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "davinci_gw.mcp"],
        cwd=project,
        env=environment,
    )

    async def scenario() -> None:
        with (tmp_path / "server-stderr.log").open("w", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "davinci-gw-mcp"
                    assert "preparation_id" in (initialized.instructions or "")[:512]
                    listed = await session.list_tools()
                    assert [tool.name for tool in listed.tools] == [
                        "get_gateway_capabilities", "validate_gateway_inputs",
                        "preview_gateway_update", "generate_gateway_arxml",
                    ]
                    capabilities = await session.call_tool("get_gateway_capabilities")
                    assert capabilities.structured_content["status"] == "SUCCESS"
                    preview = await session.call_tool("preview_gateway_update", {
                        "config_path": str(config.resolve()), "baseline_path": str(baseline.resolve()),
                    })
                    assert preview.structured_content["status"] == "SUCCESS"
                    preparation_id = preview.structured_content["preparation_id"]
                    generated = await session.call_tool("generate_gateway_arxml", {
                        "preparation_id": preparation_id, "output_path": str(output.resolve()),
                    })
                    assert generated.structured_content["status"] == "SUCCESS"
                    assert output.is_file()

    asyncio.run(scenario())


@pytest.mark.slow
def test_stdio_request_cancellation_keeps_server_usable(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """客户端取消通知应桥接到核心安全边界，且不杀死长生命周期服务。"""
    config = workbook_factory(filename="cancel_v4.84.xlsx")  # type: ignore[operator]
    baseline = arxml_factory(filename="cancel.arxml")  # type: ignore[operator]
    payload = baseline.read_bytes()
    baseline.write_bytes(payload.replace(b"</AUTOSAR>", b" " * (32 * 1024 * 1024) + b"</AUTOSAR>"))
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    environment["DAVINCI_GW_LOG_DIR"] = str(tmp_path / "logs")
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "davinci_gw.mcp"], cwd=project, env=environment,
    )

    async def scenario() -> None:
        with (tmp_path / "cancel-stderr.log").open("w", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
                    await session.initialize()
                    request = asyncio.create_task(session.call_tool("preview_gateway_update", {
                        "config_path": str(config.resolve()), "baseline_path": str(baseline.resolve()),
                    }))
                    await asyncio.sleep(0.02)
                    request.cancel()
                    with suppress(asyncio.CancelledError):
                        await request
                    # 取消后的同一进程仍能服务后续只读调用，证明没有线程/协议崩溃。
                    capabilities = await session.call_tool(
                        "get_gateway_capabilities", read_timeout_seconds=60,
                    )
                    assert capabilities.structured_content["status"] == "SUCCESS"
                    assert not list(tmp_path.glob("*.tmp.arxml"))

    asyncio.run(scenario())
