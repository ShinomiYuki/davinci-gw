"""把异步 MCP 请求桥接到同步、可协作取消的公共 Facade。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Callable, TypeVar

from davinci_gw.runtime import CancellationToken

T = TypeVar("T")


class BoundedFacadeRuntime:
    """每个服务进程最多执行一个重任务，并集中管理活动取消令牌。"""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="davinci-gw-mcp")
        self._gate = asyncio.Semaphore(1)
        self._tokens: set[CancellationToken] = set()
        self._futures: set[asyncio.Future[object]] = set()
        self._lock = RLock()
        self._closed = False

    async def run(self, operation: Callable[[CancellationToken], T]) -> T:
        """执行同步调用；MCP 任务取消时先通知核心，再等待其到达安全边界。"""
        async with self._gate:
            with self._lock:
                if self._closed:
                    raise RuntimeError("MCP runtime is closed")
            token = CancellationToken()
            loop = asyncio.get_running_loop()
            future: asyncio.Future[T] = loop.run_in_executor(self._executor, operation, token)
            with self._lock:
                self._tokens.add(token)
                self._futures.add(future)  # type: ignore[arg-type]
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                token.cancel()
                try:
                    await asyncio.shield(future)
                except (asyncio.CancelledError, Exception):
                    pass
                raise
            finally:
                with self._lock:
                    self._tokens.discard(token)
                    self._futures.discard(future)  # type: ignore[arg-type]

    def cancel_active(self) -> None:
        """幂等请求全部活动操作在下一个安全边界退出。"""
        with self._lock:
            tokens = tuple(self._tokens)
        for token in tokens:
            token.cancel()

    async def close(self) -> None:
        """用于 STDIO EOF/进程关闭：取消活动任务并等待工作线程收尾。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures)
        self.cancel_active()
        if futures:
            await asyncio.gather(*(asyncio.shield(item) for item in futures), return_exceptions=True)
        await asyncio.to_thread(self._executor.shutdown, True, cancel_futures=True)
