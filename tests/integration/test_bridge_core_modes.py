"""Bridge 通过真实核心转发的端到端行为。

覆盖两种核心形态：进程内核心与独立 STDIO 核心进程。
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from davinci_gw.bridge.config import BridgeConfig, CoreConfig
from davinci_gw.bridge.core import EmbeddedCore, StdioCore
from davinci_gw.bridge.server import build_bridge_app


async def _call_capabilities(url: str, token: str) -> dict[str, Any]:
    """通过官方 MCP 客户端在真实端口上调用一次工具。"""
    async with AsyncExitStack() as stack:
        from mcp.shared._httpx_utils import create_mcp_http_client

        http_client = await stack.enter_async_context(
            create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}),
        )
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            result = await client.call_tool("get_gateway_capabilities")
            return dict(result.structured_content)


def _serve(app: Any, port: int) -> tuple[uvicorn.Server, asyncio.Task[Any]]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    return server, asyncio.create_task(server.serve())


async def _wait_ready(server: uvicorn.Server, task: asyncio.Task[Any]) -> int:
    while not server.started:
        if task.done():
            task.result()
            raise AssertionError("桥接服务意外退出。")
        await asyncio.sleep(0.02)
    return int(server.servers[0].sockets[0].getsockname()[1])


def test_bridge_forwards_to_the_embedded_core() -> None:
    core = EmbeddedCore()
    config = BridgeConfig(host="127.0.0.1", port=0, token="token-embedded", core=CoreConfig(mode="embedded"))

    async def scenario() -> None:
        app = build_bridge_app(core, config=config)
        server, task = _serve(app, config.port)
        try:
            port = await _wait_ready(server, task)
            payload = await _call_capabilities(f"http://127.0.0.1:{port}/mcp", "token-embedded")
            assert payload["status"] == "SUCCESS"
            assert payload["product"]["name"] == "davinci-gw-mcp"
            assert payload["product"]["transport"] == "streamable-http"
            assert payload["product"]["bridge"]["core"]["mode"] == "embedded"
        finally:
            server.should_exit = True
            await task
            await core.aclose()

    asyncio.run(scenario())


def test_bridge_forwards_to_a_real_stdio_core_process() -> None:
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    core = StdioCore(sys.executable, ("-m", "davinci_gw.mcp"), environment)
    config = BridgeConfig(
        host="127.0.0.1", port=0, token="token-stdio",
        core=CoreConfig(mode="stdio", target=sys.executable, args=("-m", "davinci_gw.mcp")),
    )

    async def scenario() -> None:
        app = build_bridge_app(core, config=config)
        server, task = _serve(app, config.port)
        try:
            port = await _wait_ready(server, task)
            payload = await _call_capabilities(f"http://127.0.0.1:{port}/mcp", "token-stdio")
            assert payload["status"] == "SUCCESS"
            assert payload["product"]["name"] == "davinci-gw-mcp"
            assert payload["product"]["bridge"]["core"]["mode"] == "stdio"
        finally:
            server.should_exit = True
            await task
            await core.aclose()

    asyncio.run(scenario())
