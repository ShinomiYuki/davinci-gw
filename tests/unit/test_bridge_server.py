"""桥接服务只转发、不新增业务行为。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp import Client

from davinci_gw.bridge import auth as auth_module
from davinci_gw.bridge.config import BridgeConfig, CoreConfig
from davinci_gw.bridge.core import CoreUnavailableError
from davinci_gw.bridge.auth import BearerTokenMiddleware
from davinci_gw.bridge.server import allowed_hosts, build_bridge_app, build_bridge_server


class _PassthroughApp:
    """只回 200 的占位下游应用，用于单独验证鉴权中间件。"""

    async def __call__(self, scope, receive, send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", b"0")],
        })
        await send({"type": "http.response.body", "body": b""})


async def _status_for(app, authorization: str) -> int:
    """带指定 Authorization 头调用一次应用，返回状态码。"""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("latin-1")))
    await app({"type": "http", "method": "POST", "path": "/mcp", "headers": headers}, receive, send)
    return int(sent[0]["status"])


def test_non_ascii_authorization_header_is_rejected_with_401() -> None:
    """非 ASCII 的 Authorization 头必须回 401，不能变成 500 加一段 traceback。

    hmac.compare_digest 拒绝含非 ASCII 字符的 str；直接拿解码后的字符串去比较，
    客户端随手发一个高位字节就能把整个请求打成服务端错误。
    """
    middleware = BearerTokenMiddleware(_PassthroughApp(), "secret")
    assert asyncio.run(_status_for(middleware, "Bearer p\u00e4sswort")) == 401
    assert asyncio.run(_status_for(middleware, "Bearer \u00ff")) == 401
    # 正确的 ASCII 令牌仍然放行。
    assert asyncio.run(_status_for(middleware, "Bearer secret")) == 200


def test_rejection_drains_the_request_body_before_responding() -> None:
    """鉴权失败时必须先读完请求体再回 401。

    否则客户端还在发送 body 时服务端就关闭连接，Windows 会用 RST 中断连接，
    调用方收到的是连接错误而不是 401。
    """
    middleware = BearerTokenMiddleware(_PassthroughApp(), "secret")
    sent: list[dict[str, Any]] = []
    chunks = [
        {"type": "http.request", "body": b"part-1", "more_body": True},
        {"type": "http.request", "body": b"part-2", "more_body": False},
    ]

    async def receive() -> dict[str, Any]:
        return chunks.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def scenario() -> None:
        await middleware({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send)

    asyncio.run(scenario())
    assert sent[0]["status"] == 401
    # 请求体必须已被读完，否则调用方的连接会被中断。
    assert chunks == []


def test_rejection_keeps_draining_a_large_request_body() -> None:
    """请求体超过 1 MiB 时也要读完，不能提前放弃。

    提前停止等于在客户端还在发送时就关闭连接，Windows 会用 RST
    中断连接，调用方收到的是传输层错误而不是 401——那正是当初
    要加排空的原因。请求体是被丢弃的，不占内存，真正的上限是时限。
    """
    middleware = BearerTokenMiddleware(_PassthroughApp(), "secret")
    sent: list[dict[str, Any]] = []
    chunk = b"x" * (256 * 1024)
    remaining = 8  # 2 MiB，超过原先 1 MiB 的上限

    async def receive() -> dict[str, Any]:
        nonlocal remaining
        if remaining <= 0:
            return {"type": "http.disconnect"}
        remaining -= 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def scenario() -> None:
        await middleware({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send)

    asyncio.run(scenario())
    assert sent[0]["status"] == 401
    # 2 MiB 全部排空完，没有因超过 1 MiB 而提前放弃。
    assert remaining == 0


def test_rejection_gives_up_on_a_stalled_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """对端发一半 body 就停住时，鉴权拒绝不能一直挂着。

    ``receive()`` 在对端不再发送数据时永远不会返回；没有时限的话，
    这个 ASGI 任务与套接字会被一直占住，401 也永远发不出去。
    """
    monkeypatch.setattr(auth_module, "DRAIN_TIMEOUT_SECONDS", 0.2)
    middleware = BearerTokenMiddleware(_PassthroughApp(), "secret")
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        # 模拟对端发完第一块后停住：后续 receive 永远不返回。
        await asyncio.sleep(3600)
        raise AssertionError("不应执行到这里。")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def scenario() -> None:
        await middleware(
            {"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send,
        )

    asyncio.run(scenario())
    # 排空时限到了就放弃剩余请求体，401 仍然要如期发出。
    assert sent[0]["status"] == 401


def _envelope(tool: str, **payload: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "tool": tool,
        "operation_id": "op-1",
        "status": "SUCCESS",
        "success": True,
        "summary": "ok",
        "issue_count": 0,
        "issues_truncated": False,
        "issues": [],
        **payload,
    }


class _RecordingCore:
    def __init__(self, fail: bool = False, payload: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail
        self.payload = payload

    async def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if self.fail:
            raise CoreUnavailableError("core down")
        if self.payload is not None:
            return {**_envelope(name), **self.payload}
        return _envelope(name)

    async def aclose(self) -> None:
        return None


def _config() -> BridgeConfig:
    return BridgeConfig(core=CoreConfig(mode="embedded"))


def test_bridge_exposes_only_the_gateway_tools() -> None:
    core = _RecordingCore()
    server = build_bridge_server(core, config=_config())

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == [
                "get_gateway_capabilities",
                "validate_gateway_inputs",
                "preview_gateway_update",
                "generate_gateway_arxml",
            ]

    asyncio.run(scenario())


def test_bridge_forwards_arguments_unchanged() -> None:
    core = _RecordingCore()
    server = build_bridge_server(core, config=_config())

    async def scenario() -> None:
        async with Client(server) as client:
            await client.call_tool("preview_gateway_update", {
                "config_path": "C:/tmp/a.xlsx",
                "baseline_path": "C:/tmp/a.arxml",
                "doip_missing_policy": "SKIP",
            })
            await client.call_tool("generate_gateway_arxml", {
                "preparation_id": "prep-1", "output_path": "C:/tmp/out.arxml",
            })

    asyncio.run(scenario())
    assert core.calls[0] == ("preview_gateway_update", {
        "config_path": "C:/tmp/a.xlsx",
        "baseline_path": "C:/tmp/a.arxml",
        "doip_missing_policy": "SKIP",
    })
    assert core.calls[1] == ("generate_gateway_arxml", {
        "preparation_id": "prep-1", "output_path": "C:/tmp/out.arxml",
    })


def test_capabilities_describe_the_bridge_and_its_core() -> None:
    core = _RecordingCore()
    server = build_bridge_server(core, config=_config())
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("get_gateway_capabilities")
            captured["product"] = result.structured_content["product"]

    asyncio.run(scenario())
    product = captured["product"]
    assert product["transport"] == "streamable-http"
    assert product["bridge"]["core"]["mode"] == "embedded"
    assert product["bridge"]["endpoint"].endswith("/mcp")


def test_capabilities_do_not_leak_core_credentials() -> None:
    """能力接口把核心描述发给任意已鉴权客户端，不能夹带核心的凭据。"""
    core = _RecordingCore()
    config = BridgeConfig(core=CoreConfig(
        mode="http", target="https://alice:hunter2@core.internal/mcp?token=abc123",
    ))
    server = build_bridge_server(core, config=config)
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("get_gateway_capabilities")
            captured["product"] = result.structured_content["product"]

    asyncio.run(scenario())
    text = str(captured["product"])
    assert "hunter2" not in text
    assert "abc123" not in text


def test_capabilities_use_the_actually_bound_port() -> None:
    """端口为 0 时能力接口必须给出实际绑定的端口，而不是 :0。"""
    core = _RecordingCore()
    config = BridgeConfig(port=0, core=CoreConfig(mode="embedded"))
    server = build_bridge_server(core, config=config, port_provider=lambda: 51234)
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("get_gateway_capabilities")
            captured["product"] = result.structured_content["product"]

    asyncio.run(scenario())
    endpoint = captured["product"]["bridge"]["endpoint"]
    assert ":51234/mcp" in endpoint
    assert ":0/" not in endpoint


def test_capabilities_only_advertise_the_tools_the_bridge_exposes() -> None:
    """能力清单里的工作流不得包含桥接没有注册的修复类工具。"""
    core = _RecordingCore(payload={"workflow": [
        "validate_gateway_inputs", "preview_gateway_update", "generate_gateway_arxml",
        "diagnose_generation_failure", "start_bug_repair", "get_bug_repair_status",
        "submit_bug_repair", "cancel_bug_repair",
    ]})
    server = build_bridge_server(core, config=_config())
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("get_gateway_capabilities")
            captured["payload"] = result.structured_content

    asyncio.run(scenario())
    workflow = captured["payload"]["workflow"]
    assert workflow == [
        "validate_gateway_inputs", "preview_gateway_update", "generate_gateway_arxml",
    ]


def test_allowed_hosts_covers_registered_client_names() -> None:
    """对外监听时客户端可能用可路由 IP 或域名访问，这些必须放行。"""
    hosts = allowed_hosts("0.0.0.0", ["192.168.1.10", "gateway.internal"])
    assert "192.168.1.10" in hosts
    assert "192.168.1.10:*" in hosts
    assert "gateway.internal:*" in hosts
    # 回环监听维持原有白名单。
    assert allowed_hosts("127.0.0.1") == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


def test_allowed_hosts_bracket_ipv6_literals() -> None:
    """IPv6 监听地址的 Host 模式必须带方括号，否则匹配不上实际请求。"""
    hosts = allowed_hosts("2001:db8::1")
    assert "[2001:db8::1]" in hosts
    assert "[2001:db8::1]:*" in hosts
    # 登记的客户端地址同样处理。
    assert "[2001:db8::2]:*" in allowed_hosts("0.0.0.0", ["2001:db8::2"])


def test_core_failure_becomes_a_controlled_envelope() -> None:
    core = _RecordingCore(fail=True)
    server = build_bridge_server(core, config=_config())
    captured: dict[str, Any] = {}

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("validate_gateway_inputs", {
                "config_path": "C:/tmp/a.xlsx", "baseline_path": "C:/tmp/a.arxml",
            })
            captured["payload"] = result.structured_content

    asyncio.run(scenario())
    payload = captured["payload"]
    assert payload["status"] == "INTERNAL_FAILURE"
    assert payload["success"] is False
    assert payload["issues"][0]["code"] == "BRIDGE_CORE_UNAVAILABLE"


def test_authentication_token_equals_the_discovery_descriptor_token() -> None:
    """发现文件里的令牌必须与鉴权使用的令牌逐字一致，否则客户端一律 401。"""
    config = BridgeConfig(core=CoreConfig(mode="embedded"))
    app = build_bridge_app(_RecordingCore(), config=config)
    assert app.expected_token == config.effective_token


def test_bearer_middleware_accepts_the_discovery_token_only() -> None:
    """带正确令牌放行，缺少或错误令牌一律 401。"""
    config = BridgeConfig(core=CoreConfig(mode="embedded"))

    async def scenario() -> None:
        app = BearerTokenMiddleware(_PassthroughApp(), config.effective_token)
        assert await _status_for(app, f"Bearer {config.effective_token}") == 200
        assert await _status_for(app, "Bearer wrong-token") == 401
        assert await _status_for(app, "") == 401

    asyncio.run(scenario())
