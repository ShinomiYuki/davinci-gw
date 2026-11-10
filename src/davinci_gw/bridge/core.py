"""桥接服务的核心客户端。

桥接自身不实现任何路由规则，只把工具调用转发给核心。核心有三种形态：

- ``embedded``：与 MCP STDIO 服务共用同一套应用层逻辑，进程内调用；
- ``stdio``：独立的 MCP 子进程，通过 stdin/stdout 通信；
- ``http``：内网另一台机器上的 MCP 服务，通过 Streamable HTTP 通信。

三种形态都返回同一份有界 JSON 信封，桥接只做转发与鉴权。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping, Protocol

from davinci_gw.bridge.config import BridgeConfigError, CoreConfig

LOGGER = logging.getLogger("davinci_gw.bridge.core")

BRIDGE_TOOL_NAMES = (
    "get_gateway_capabilities",
    "validate_gateway_inputs",
    "preview_gateway_update",
    "generate_gateway_arxml",
)


class CoreUnavailableError(RuntimeError):
    """核心不可达、不支持该工具，或返回了无法解析的结果。"""


async def _await_with_timeout(awaitable: Any, timeout_seconds: float | None, label: str) -> Any:
    """在可选超时下等待一个 awaitable，超时统一转为核心不可用。"""
    try:
        if timeout_seconds is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout_seconds)
    except asyncio.TimeoutError as exc:
        LOGGER.error("core call timeout label=%s timeout=%.3fs", label, timeout_seconds)
        raise CoreUnavailableError(f"核心调用超时（{timeout_seconds} 秒）：{label}") from exc


def _consume_task_result(task: asyncio.Future[Any]) -> None:
    """取出后台任务的异常，避免留下"任务异常从未被取回"的告警。"""
    if not task.cancelled():
        task.exception()


async def _await_with_deadline(awaitable: Any, timeout_seconds: float | None, label: str) -> Any:
    """到点即返回，不等底层收尾。

    ``asyncio.wait_for`` 超时时必须等被取消的协程真正结束才返回。进程内
    核心把重活放在工作线程里，取消之后运行时还要等线程到达安全边界
    （``BoundedFacadeRuntime.run`` 会等待被屏蔽的执行器任务），一次长时间
    的配置校验或 ARXML 解析会让 ``wait_for`` 一起被拖住——配置的时限就
    形同虚设了。

    这里改成"到点就走"：时限一到立刻返回核心不可用，后台任务自行收尾。
    工作线程仍由运行时的信号量串行化，清理期间不会并发跑第二个重任务。

    调用方主动取消时同样要把取消传给子任务：否则核心收不到
    ``BoundedFacadeRuntime`` 用来通知工作线程的取消信号，调用方已经
    放弃这次调用之后，核心还会把 ARXML 写出来。
    """
    if timeout_seconds is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        # 调用方放弃这次调用：把取消传下去，让核心尽快收手。
        task.cancel()
        task.add_done_callback(_consume_task_result)
        raise
    if task in done:
        return task.result()
    LOGGER.error("core call timeout label=%s timeout=%.3fs", label, timeout_seconds)
    task.cancel()
    task.add_done_callback(_consume_task_result)
    raise CoreUnavailableError(f"核心调用超时（{timeout_seconds} 秒）：{label}")


class CoreClient(Protocol):
    """桥接对核心的最小要求。"""

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """调用核心工具并返回有界 JSON 信封。"""

    async def aclose(self) -> None:
        """释放核心会话。"""


def _extract_payload(result: Any) -> dict[str, Any]:
    """从 MCP 调用结果中取出结构化 JSON 信封。"""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise CoreUnavailableError("核心返回了无法解析的结果。")


class EmbeddedCore:
    """进程内核心：复用 MCP 服务的应用层逻辑，不引入额外 IPC。"""

    def __init__(self, service: Any | None = None, *, timeout_seconds: float | None = None) -> None:
        if service is None:
            from davinci_gw.mcp.service import GatewayMcpService

            service = GatewayMcpService()
        self._service = service
        self._timeout_seconds = timeout_seconds

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {
            "get_gateway_capabilities": self._service.get_gateway_capabilities,
            "validate_gateway_inputs": self._service.validate_gateway_inputs,
            "preview_gateway_update": self._service.preview_gateway_update,
            "generate_gateway_arxml": self._service.generate_gateway_arxml,
        }
        handler = handlers.get(name)
        if handler is None:
            raise CoreUnavailableError(f"核心不支持工具 {name}。")
        payload = handler(**dict(arguments))
        if hasattr(payload, "__await__"):
            # 进程内核心的重活在工作线程里跑：时限必须"到点就走"，
            # 否则 wait_for 会被线程收尾拖住，超时形同虚设。
            payload = await _await_with_deadline(payload, self._timeout_seconds, name)
        return dict(payload)

    async def aclose(self) -> None:
        close = getattr(self._service, "close", None)
        if close is None:
            return
        result = close()
        if hasattr(result, "__await__"):
            await result


class _SessionCore:
    """基于 MCP 客户端会话的核心：会话在独立长驻任务中建立并复用。

    会话必须在同一个任务里进入和退出（MCP 客户端内部使用 anyio 取消域），
    因此这里用专属任务承载会话生命周期；关闭时只发信号并等待该任务收尾。
    """

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self._client: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._lock: asyncio.Lock | None = None
        self._timeout_seconds = timeout_seconds
        # 每一代会话任务与它的停止信号：作废某一代时要能停掉那一代，
        # 否则被取代的会话任务会一直挂着（STDIO 核心则留下孤儿子进程）。
        self._session_stops: dict[asyncio.Task[None], asyncio.Event] = {}

    async def _make_client(self) -> Any:
        raise NotImplementedError

    async def _session_task(self) -> None:
        # 就绪信号与停止信号先取到局部变量：会话失效时属性会被置空，
        # 旧任务收尾时仍要用得到这两个事件。
        ready = self._ready
        stop = self._stop
        assert ready is not None
        assert stop is not None
        try:
            async with await self._make_client() as client:
                self._client = client
                ready.set()
                await stop.wait()
        except BaseException as exc:  # noqa: BLE001 - 记录后由调用方判断
            self._error = exc
            ready.set()
        finally:
            # 这一代会话结束了，登记项不再需要——否则字典会随
            # 核心重建次数一直增长。
            current = asyncio.current_task()
            if current is not None:
                self._session_stops.pop(current, None)

    async def start(self) -> None:
        """建立核心会话；并发首次调用共享同一次建连。

        多个请求同时到达时，只允许其中一个真正建立会话，其余请求等待
        同一个就绪信号后再继续，避免出现“核心会话不可用”的裸失败。

        上一次建连失败时会话任务已经结束、就绪信号也已置位。若不清掉
        这份状态，后续请求会复用一个注定失败的任务，永远拿回同一个错误，
        只有重启 Bridge 才能恢复。这里在重连前把失败状态复位。

        建连本身也要受 ``--core-timeout`` 约束：HTTP 或 STDIO 核心的
        握手若迟迟不完成，不能让调用无限期挂起。
        """
        if self._client is not None:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._client is not None:
                return
            if self._task is not None and self._task.done():
                # 任务已结束说明上一次会话没能建立起来（失败或被取消）。
                self._session_stops.pop(self._task, None)
                self._task = None
                self._ready = None
                self._stop = None
                self._error = None
            if self._task is None:
                self._ready = asyncio.Event()
                self._stop = asyncio.Event()
                self._error = None
                self._task = asyncio.create_task(self._session_task())
                # 登记这一代的停止信号：作废某一代时要能停掉那一代。
                self._session_stops[self._task] = self._stop
            ready = self._ready
            session_task = self._task
        assert ready is not None
        try:
            await _await_with_timeout(
                ready.wait(), self._timeout_seconds, "core session startup",
            )
        except CoreUnavailableError:
            # 握手超时：取消这次建连并清掉状态，让后续调用能重新建立连接，
            # 而不是永远复用一个挂起的会话任务。
            if self._task is session_task:
                session_task.cancel()
                # 取消后不等待它结束，但要取出它的异常，
                # 否则会留下"任务异常从未被取回"的告警。
                session_task.add_done_callback(_consume_task_result)
                self._session_stops.pop(session_task, None)
                self._task = None
                self._ready = None
                self._stop = None
                self._error = None
            raise
        if self._client is None:
            raise CoreUnavailableError("核心会话启动失败。") from self._error

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._client is None:
            await self.start()
        client = self._client
        session_task = self._task
        if client is None:
            raise CoreUnavailableError("核心会话不可用。")
        try:
            result = await _await_with_timeout(
                client.call_tool(name, dict(arguments)), self._timeout_seconds, name,
            )
        except CoreUnavailableError:
            raise
        except Exception as exc:
            LOGGER.error("core tool call failed tool=%s error_type=%s", name, type(exc).__name__)
            # 会话建立后核心中途消失（重启、崩溃、网络中断）时，这份连接
            # 已经不可用。留着它，后续调用会一直复用同一个坏会话，
            # 核心恢复后也连不回去，只有重启 Bridge 才能恢复。
            self._invalidate_session(session_task)
            raise CoreUnavailableError(f"核心调用失败：{name}") from exc
        if getattr(result, "isError", False):
            LOGGER.error("core returned error tool=%s", name)
        return _extract_payload(result)

    def _invalidate_session(self, session_task: asyncio.Task[None] | None = None) -> None:
        """丢弃当前会话，让下一次调用重新建立连接。

        只清理本次调用所用的会话，避免把并发调用刚建立的新会话误清掉。

        「本次调用所用的会话」可能已经不是当前这一代了（期间核心重建过）。
        早先的实现在这种情况下直接返回，那一代既没收到停止信号也没被
        取消——按当前代码的调用序列，这种情况还不可达（每一代被取代时
        要么已经结束，要么会被 stop / cancel 掉），但那依赖的是「恰好
        每次取代都收尾」这个不显式的约定。

        这里按「代次」把该停的那一代停掉：任务与它的停止信号一起登记，
        失效时把该代的信号置位，让它自己收尾。这样即便将来新增了一条
        取代会话的路径、漏了收尾，被取代的那一代也不会一直挂着——对
        STDIO 核心来说，挂着就等于占着一个核心子进程。
        """
        if session_task is not None and self._task is not session_task:
            # 失效的是更早的一代：只停掉那一代，不动当前这一代。
            stop = self._session_stops.pop(session_task, None)
            if stop is not None:
                stop.set()
            return
        task = self._task
        stop = self._stop
        self._client = None
        self._task = None
        self._ready = None
        self._stop = None
        self._error = None
        if task is not None and stop is not None:
            self._session_stops.pop(task, None)
            stop.set()

    async def aclose(self) -> None:
        task = self._task
        stop = self._stop
        self._task = None
        self._client = None
        self._lock = None
        self._ready = None
        self._stop = None
        # 关闭时把还登记着的每一代都停掉：被取代的旧会话若仍在挂着，
        # 它们占着的核心子进程会一直活到 Bridge 退出。
        for pending_task, pending_stop in list(self._session_stops.items()):
            pending_stop.set()
            if pending_task is not task and not pending_task.done():
                pending_task.cancel()
                pending_task.add_done_callback(_consume_task_result)
            self._session_stops.pop(pending_task, None)
        if task is None:
            return
        if stop is not None:
            stop.set()
        try:
            await task
        except Exception as exc:  # pragma: no cover - 关闭期异常不影响退出
            LOGGER.error("core session close failed error_type=%s", type(exc).__name__)


class StdioCore(_SessionCore):
    """独立核心进程：通过 STDIO 通信，核心可单独升级。"""

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self._command = command
        self._args = tuple(args)
        self._env = dict(env) if env else None

    async def _make_client(self) -> Any:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        parameters = StdioServerParameters(command=self._command, args=list(self._args), env=self._env)
        return Client(parameters)


class HttpCore(_SessionCore):
    """内网独立核心：通过 Streamable HTTP 通信，核心可单独部署升级。"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self._url = url
        self._token = token

    async def _make_client(self) -> Any:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        http_client = _create_http_client(headers)
        return Client(streamable_http_client(self._url, http_client=http_client))


def _create_http_client(headers: dict[str, str] | None) -> Any:
    """创建带 MCP 默认超时的 HTTP 客户端；必要时退回普通客户端。"""
    try:
        from mcp.shared._httpx_utils import create_mcp_http_client

        return create_mcp_http_client(headers=headers)
    except Exception:  # pragma: no cover - 仅在 SDK 结构变化时触发
        # SDK 的辅助函数不可用时退回普通客户端。不同 SDK 版本依赖的
        # 客户端模块名不同（httpx2 / httpx），按实际装到的那个来，
        # 写死其中一个会在另一种环境里直接启动失败。
        for module_name in ("httpx2", "httpx"):
            try:
                module = __import__(module_name)
            except ImportError:
                continue
            return module.AsyncClient(headers=headers)
        raise CoreUnavailableError(
            "缺少可用的 HTTP 客户端模块：SDK 辅助函数不可用，且未安装 httpx2 或 httpx。"
        ) from None


def build_core(config: CoreConfig) -> CoreClient:
    """按配置创建核心客户端。"""
    config.validate()
    timeout = config.timeout_seconds
    if config.mode == "embedded":
        return EmbeddedCore(timeout_seconds=timeout)
    if config.mode == "stdio":
        assert config.target is not None
        return StdioCore(config.target, config.args, config.env, timeout_seconds=timeout)
    if config.mode == "http":
        assert config.target is not None
        return HttpCore(config.target, config.token, timeout_seconds=timeout)
    raise BridgeConfigError(f"未知的核心模式：{config.mode}")
