"""桥接服务在真实内部端口上的端到端行为。"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import ssl
import urllib.error
import urllib.request
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from davinci_gw.bridge.__main__ import _serve
from davinci_gw.bridge.config import BridgeConfig, CoreConfig
from davinci_gw.bridge.server import build_bridge_app


class _StubCore:
    """不接触文件的核心替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {
            "schema_version": "1.0",
            "tool": name,
            "operation_id": "op-1",
            "status": "SUCCESS",
            "success": True,
            "summary": "ok",
            "issue_count": 0,
            "issues_truncated": False,
            "issues": [],
        }

    async def aclose(self) -> None:
        return None


def _post_without_token(url: str, token: str | None = None) -> int:
    """发起一个 MCP 请求，返回状态码；令牌为 None 时不带认证头。"""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def test_bridge_serves_mcp_over_an_internal_port_with_token_auth() -> None:
    import uvicorn

    core = _StubCore()
    config = BridgeConfig(host="127.0.0.1", port=0, token="test-token", core=CoreConfig(mode="embedded"))
    config.validate()

    async def scenario() -> None:
        app = build_bridge_app(core, config=config)
        uvicorn_config = uvicorn.Config(
            app, host=config.host, port=config.port, log_level="warning", access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                if task.done():
                    task.result()
                    raise AssertionError("桥接服务意外退出。")
                await asyncio.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}/mcp"

            # 无令牌与错误令牌都必须被拒绝。
            # 阻塞式请求必须放到线程中，否则会饿死同循环里的服务端。
            assert await asyncio.to_thread(_post_without_token, url) == 401
            assert await asyncio.to_thread(_post_without_token, url, "wrong-token") == 401

            # 正确令牌可以通过官方 MCP 客户端完成握手与调用。
            async with AsyncExitStack() as stack:
                from mcp.shared._httpx_utils import create_mcp_http_client

                http_client = await stack.enter_async_context(
                    create_mcp_http_client(headers={"Authorization": "Bearer test-token"}),
                )
                transport = streamable_http_client(url, http_client=http_client)
                async with Client(transport) as client:
                    listed = await client.list_tools()
                    assert [tool.name for tool in listed.tools] == [
                        "get_gateway_capabilities",
                        "validate_gateway_inputs",
                        "preview_gateway_update",
                        "generate_gateway_arxml",
                    ]
                    result = await client.call_tool("get_gateway_capabilities")
                    payload = result.structured_content
                    assert payload["status"] == "SUCCESS"
                    assert payload["product"]["transport"] == "streamable-http"
                    assert payload["product"]["bridge"]["endpoint"].endswith("/mcp")
        finally:
            server.should_exit = True
            await task

    asyncio.run(scenario())
    assert core.calls == [("get_gateway_capabilities", {})]


def _self_signed_pair(tmp_path: Path) -> tuple[Path, Path]:
    """生成一份自签证书：TLS 运行时路径必须在真实证书下验证。"""
    pytest.importorskip("cryptography", reason="缺少 cryptography，无法生成自签证书")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return cert_path, key_path


def _http_client_module() -> Any:
    """MCP SDK 依赖的 HTTP 客户端模块名在不同版本里不一样。"""
    for module_name in ("httpx2", "httpx"):
        try:
            return __import__(module_name)
        except ImportError:
            continue
    pytest.skip("当前环境没有可用的 HTTP 客户端模块（httpx2 / httpx）")


def _post_status(url: str, token: str | None, context: ssl.SSLContext | None = None) -> int:
    """发起一个 MCP 请求，返回状态码；令牌为 None 时不带认证头。"""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "davinci-gw-tests", "version": "1"},
        },
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def test_bridge_serves_mcp_over_tls_and_refuses_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """开了 TLS 就必须真的走 TLS：明文请求打不进去。

    这是「对外监听必须启用 TLS」的落点。配置层只保证对外通告的地址写的是
    https，运行时若忘了把证书交给 Uvicorn，令牌与路由结果就会在链路上裸奔，
    而配置层的测试照样全绿。
    """
    import uvicorn

    cert_path, key_path = _self_signed_pair(tmp_path)
    descriptor = tmp_path / "bridge.json"
    config = BridgeConfig(
        host="127.0.0.1", port=0, path="/mcp", token="tls-token",
        core=CoreConfig(mode="embedded"), descriptor_path=descriptor,
        tls_certfile=str(cert_path), tls_keyfile=str(key_path),
    )
    config.validate()
    assert config.tls_enabled
    assert config.url_for().startswith("https://")

    # 抓住生产启动路径里建出来的 Uvicorn 实例，好让它优雅停机。
    started: list[Any] = []
    original_server = uvicorn.Server

    def _capture(server_config: Any) -> Any:
        server = original_server(server_config)
        started.append(server)
        return server

    monkeypatch.setattr(uvicorn, "Server", _capture)

    async def scenario() -> None:
        task = asyncio.create_task(_serve(config, "warning"))
        try:
            deadline = asyncio.get_running_loop().time() + 60
            while not descriptor.exists():
                if task.done():
                    task.result()
                    raise AssertionError("桥接服务意外退出。")
                await asyncio.sleep(0.05)
                if asyncio.get_running_loop().time() > deadline:
                    raise AssertionError("等待客户端发现文件超时。")
            await asyncio.sleep(0.2)
            payload = json.loads(descriptor.read_text(encoding="utf-8"))
            url = payload["url"]
            assert url.startswith("https://"), f"发现文件里通告的不是 https 地址：{url}"

            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            # 无令牌与错误令牌仍然被拒。
            assert await asyncio.to_thread(_post_status, url, None, context) == 401
            assert await asyncio.to_thread(_post_status, url, "wrong-token", context) == 401

            # 正确令牌可以走完 HTTPS 握手与调用。
            module = _http_client_module()
            async with AsyncExitStack() as stack:
                http_client = await stack.enter_async_context(module.AsyncClient(
                    verify=context, headers={"Authorization": "Bearer tls-token"},
                ))
                transport = streamable_http_client(url, http_client=http_client)
                async with Client(transport) as client:
                    result = await client.call_tool("get_gateway_capabilities")
                    assert result.structured_content["status"] == "SUCCESS"

            # 明文请求必须打不进去：这是「对外监听必须启用 TLS」的实际落点。
            plain_url = url.replace("https://", "http://")
            with pytest.raises((urllib.error.URLError, OSError)):
                await asyncio.to_thread(_post_status, plain_url, "tls-token")
        finally:
            for server in started:
                server.should_exit = True
            await task

    asyncio.run(scenario())
