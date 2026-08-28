"""隔离观察者故障并输出单调递增的中立进度事件。"""

from __future__ import annotations

from typing import Protocol

from davinci_gw.contracts import ProgressEventDto


class ProgressObserver(Protocol):
    """GUI、MCP 或测试可实现的同步进度观察协议。"""
    def on_progress(self, event: ProgressEventDto) -> None: ...


class NoOpProgressObserver:
    """调用方未提供观察者时使用的默认空实现。"""
    def on_progress(self, event: ProgressEventDto) -> None:
        del event


class ProgressReporter:
    """观察者异常永远不能改变核心操作结果。"""

    def __init__(self, operation_id: str, observer: ProgressObserver | None = None) -> None:
        self.operation_id = operation_id
        self.observer = observer or NoOpProgressObserver()
        self.sequence = 0

    def emit(
        self, stage_id: str, stage_name: str, current: int, total: int, *, cancellable: bool = True,
    ) -> ProgressEventDto:
        """创建单调事件并隔离观察者异常。"""
        self.sequence += 1
        percent = 100.0 if total <= 0 else round(current * 100.0 / total, 2)
        event = ProgressEventDto(
            self.operation_id, self.sequence, stage_id, stage_name, current, total, percent, cancellable,
        )
        try:
            self.observer.on_progress(event)
        except Exception:
            pass
        return event
