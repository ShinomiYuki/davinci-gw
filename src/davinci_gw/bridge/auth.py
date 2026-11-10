"""桥接服务的访问令牌校验。

桥接在内部端口上对外提供 MCP 端点，任何能访问该端口的进程都可能
发起调用，因此所有请求都必须携带启动时确定的 Bearer 令牌。
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any, Awaitable, Callable, MutableMapping

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

# 排空请求体的整体时限（秒）。正常客户端的 body 远小于这个量级，
# 超时只针对"发一半就停住"的异常对端。
DRAIN_TIMEOUT_SECONDS = 5.0


class BearerTokenMiddleware:
    """校验 ``Authorization: Bearer <token>`` 的纯 ASGI 中间件。"""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    @property
    def expected_token(self) -> str:
        """本次启动要求的令牌；与客户端发现文件中的令牌必须一致。"""
        return self._token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        if not self._authorized(scope.get("headers") or []):
            await self._reject(receive, send)
            return
        await self._app(scope, receive, send)

    def _authorized(self, headers: list[tuple[bytes, bytes]]) -> bool:
        for raw_name, raw_value in headers:
            if raw_name.decode("latin-1").lower() != "authorization":
                continue
            scheme, _, value = raw_value.decode("latin-1").partition(" ")
            if scheme.lower() != "bearer":
                return False
            # 必须按字节比较：hmac.compare_digest 拒绝含非 ASCII 字符的 str，
            # 客户端随手发一个高位字节就会让整个请求变成 500 加一段 traceback。
            # latin-1 与字节一一对应，这里编码回去就是客户端真正发来的字节。
            candidate = value.strip().encode("latin-1", "replace")
            expected = self._token.encode("latin-1", "replace")
            return hmac.compare_digest(candidate, expected)
        return False

    async def _reject(self, receive: Receive, send: Send) -> None:
        # 先排空请求体再回应：否则客户端还在发送 body 时服务端就关闭连接，
        # Windows 会用 RST 中断连接，调用方收到的是连接错误而不是 401。
        await self._drain_body(receive)
        body = b'{"error":"unauthorized"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _drain_body(receive: Receive) -> None:
        """读取并丢弃请求体，直到读完或到达整体截止时间。

        排空必须有时限：对端发了一部分 body 就停住且不关闭连接时，
        ``receive()`` 永远不会返回，鉴权拒绝也就永远发不出去，
        请求方会把一个 ASGI 任务与套接字一直占住。这里给整体排空
        设一个截止时间，超时后直接放弃剩余请求体并回应 401。

        这里没有体积上限：请求体是被丢弃的，不占内存，提前停止反而会
        让服务端在客户端还在发送时就关闭连接，又回到 Windows 用 RST
        中断连接的老问题上。真正的上限是截止时间——异常客户端最多
        占用这么久。
        """
        deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                # 对端不再发送数据：放弃剩余请求体，鉴权结论不受影响。
                return
            if message["type"] != "http.request":
                return
            if not message.get("more_body", False):
                return
