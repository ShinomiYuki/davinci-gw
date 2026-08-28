"""提供线程安全、协作式且无外部依赖的取消令牌。"""

from __future__ import annotations

from threading import Event


class OperationCancelled(RuntimeError):
    """仅在应用内部传播的协作式取消信号。"""

    def __init__(self, stage_id: str) -> None:
        super().__init__(stage_id)
        self.stage_id = stage_id


class CancellationToken:
    """调用方可跨线程请求取消，执行方只在安全边界响应。"""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def is_cancelled(self) -> bool:
        """返回调用方是否已请求取消。"""
        return self._event.is_set()

    def cancel(self) -> None:
        """幂等地请求取消。"""
        self._event.set()

    def checkpoint(self, stage_id: str) -> None:
        """仅在调用方选择的安全边界中断内部流程。"""
        if self.is_cancelled:
            raise OperationCancelled(stage_id)
