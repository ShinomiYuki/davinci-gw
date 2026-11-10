"""核心会话的生命周期回归。

锁住一条不变量：**任何一代会话任务，都不会在被取代之后一直挂着**。

会话被取代时，旧实现可能让那一代既没收到停止信号也没被取消。
按当前的调用序列，这种情况还不可达（每一代被取代时要么已经结束，
要么会被 stop / cancel 掉），但那依赖的是「恰好每次取代都收尾」
这个不显式的约定。一旦将来新增一条取代会话的路径并漏了收尾，
被取代的那一代就会一直挂着——对 STDIO 核心来说，挂着就等于占着
一个核心子进程。

这里的断言落在真实可观测点上：会话任务是否结束。

仓库没有装 pytest-asyncio，异步用例按既有约定用 asyncio.run 驱动。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest

from davinci_gw.bridge.core import CoreUnavailableError, _SessionCore


class _FakeClient:
    """最小异步上下文管理器，模拟 MCP 客户端会话。"""

    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True


class _CountingCore(_SessionCore):
    """记录建立过几代会话，并保留每一代的任务。"""

    def __init__(self) -> None:
        super().__init__(timeout_seconds=5.0)
        self.sessions = 0
        self.handshake_delay = 0.0

    async def _make_client(self) -> object:
        self.sessions += 1
        if self.handshake_delay:
            await asyncio.sleep(self.handshake_delay)
        return _FakeClient()


async def _replace_session(core: _CountingCore) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    """建立两代会话，返回（第一代任务，第二代任务）。"""
    await core.start()
    first = core._task
    assert first is not None
    # 丢掉当前状态，让下一次 start() 重建一代。
    core._client = None
    core._task = None
    core._ready = None
    core._stop = None
    await core.start()
    second = core._task
    assert second is not None
    return first, second


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_invalidating_an_older_session_stops_that_generation() -> None:
    """作废旧一代会话时，那一代必须被真正停掉。

    这条锁的是不变量而不是某个具体缺陷：旧实现在这里直接返回，
    那一代就既没收到停止信号也没被取消。只要将来新增一条取代会话
    的路径漏了收尾，这条用例会立刻失败。
    """

    async def scenario() -> None:
        core = _CountingCore()
        try:
            first, second = await _replace_session(core)
            assert core._task is second

            # 作废第一代（它已经不是当前这一代了）。
            core._invalidate_session(first)

            # 第一代必须结束，而不是一直挂着。
            await asyncio.wait_for(asyncio.shield(first), timeout=5)
            assert first.done(), "被取代的会话任务没有结束，它会一直占着核心子进程"
            # 当前这一代不受影响。
            assert core._task is second
            assert not second.done(), "作废旧一代时把当前这一代也弄没了"
        finally:
            await core.aclose()

    _run(scenario)


def test_session_stop_registry_does_not_grow_without_bound() -> None:
    """会话登记表不能随核心重建次数一直增长。"""

    async def scenario() -> None:
        core = _CountingCore()
        try:
            for _ in range(5):
                await core.start()
                previous = core._task
                assert previous is not None
                # 结束这一代并重建。
                previous.cancel()
                previous.add_done_callback(lambda _task: None)
                await asyncio.sleep(0.05)
                core._client = None
                core._task = None
                core._ready = None
                core._stop = None
            await asyncio.sleep(0.1)
            assert len(core._session_stops) <= 1, core._session_stops
        finally:
            await core.aclose()

    _run(scenario)


def test_closing_cancels_every_pending_session_generation() -> None:
    """关闭时所有仍在挂着的会话都要被停掉。

    锁的是不变量：Bridge 退出后不该还有会话任务占着核心子进程。
    """

    async def scenario() -> None:
        core = _CountingCore()
        first, second = await _replace_session(core)

        await core.aclose()

        await asyncio.wait_for(asyncio.shield(first), timeout=5)
        assert first.done(), "关闭后仍有会话任务挂着"
        assert second.done(), "关闭后当前会话任务仍在挂着"

    _run(scenario)


def test_concurrent_first_calls_share_one_session() -> None:
    """并发首调只建立一次会话，全部成功。"""

    async def scenario() -> None:
        core = _CountingCore()
        try:
            results = await asyncio.gather(*[core.start() for _ in range(8)])
            assert not [item for item in results if isinstance(item, BaseException)]
            assert core.sessions == 1, core.sessions
        finally:
            await core.aclose()

    _run(scenario)


def test_a_failed_handshake_can_be_retried() -> None:
    """握手失败后要能重新建立连接，而不是永远复用一个坏会话。"""

    async def scenario() -> None:
        core = _CountingCore()
        core._timeout_seconds = 0.2
        # 让握手比时限更久，触发握手超时。
        core.handshake_delay = 10.0
        try:
            with pytest.raises(CoreUnavailableError):
                await core.start()
            # 第二次握手不再超时，应当成功。
            core.handshake_delay = 0.0
            core._timeout_seconds = 5.0
            await core.start()
            assert core.sessions == 2, core.sessions
        finally:
            await core.aclose()

    _run(scenario)
