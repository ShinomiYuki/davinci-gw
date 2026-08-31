"""单一长驻工作线程中的 Facade 执行器。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot

from davinci_gw.application.facade import GatewayFacade
from davinci_gw.contracts import ProgressEventDto, UpdateRequestDto
from davinci_gw.runtime import CancellationToken

LOGGER = logging.getLogger("davinci_gw.gui.worker")


class _ProgressBridge:
    def __init__(self, callback) -> None:
        self._callback = callback

    def on_progress(self, event: ProgressEventDto) -> None:
        self._callback(event)


class _GatewayWorker(QObject):
    """在所属 QThread 内创建、使用并释放 GatewayFacade。"""

    progress = Signal(object)
    preview_finished = Signal(str, object)
    generation_finished = Signal(str, object)
    discard_finished = Signal(str)
    shutdown_ready = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._facade: GatewayFacade | None = None
        self._sessions: set[str] = set()

    @Slot()
    def initialize(self) -> None:
        self._facade = GatewayFacade()

    @Slot(str, str, str, object)
    def preview(self, task_id: str, config_path: str, baseline_path: str, token: CancellationToken) -> None:
        assert self._facade is not None
        result = self._facade.preview(
            UpdateRequestDto(config_path, baseline_path),
            observer=_ProgressBridge(self.progress.emit),
            cancellation=token,
        )
        if result.session_id:
            self._sessions.add(result.session_id)
        self.preview_finished.emit(task_id, result)

    @Slot(str, str, str, object)
    def commit(self, task_id: str, session_id: str, output_path: str, token: CancellationToken) -> None:
        assert self._facade is not None
        result = self._facade.commit_prepared(
            session_id,
            output_path,
            overwrite=False,
            observer=_ProgressBridge(self.progress.emit),
            cancellation=token,
        )
        self._sessions.discard(session_id)
        self.generation_finished.emit(task_id, result)

    @Slot(str)
    def discard(self, session_id: str) -> None:
        if self._facade is not None:
            self._facade.discard_prepared(session_id)
        self._sessions.discard(session_id)
        self.discard_finished.emit(session_id)

    @Slot()
    def shutdown(self) -> None:
        if self._facade is not None:
            for session_id in tuple(self._sessions):
                self._facade.discard_prepared(session_id)
            self._sessions.clear()
        self._facade = None
        self.shutdown_ready.emit()


class QtTaskRunner(QObject):
    """主线程侧的单任务门闩和协作式取消入口。"""

    progress = Signal(object)
    preview_finished = Signal(object)
    generation_finished = Signal(object)
    stopped = Signal()

    _preview_requested = Signal(str, str, str, object)
    _commit_requested = Signal(str, str, str, object)
    _discard_requested = Signal(str)
    _shutdown_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _GatewayWorker()
        self._worker.moveToThread(self._thread)
        self._busy = False
        self._task_id = ""
        self._token: CancellationToken | None = None
        self._shutdown_pending = False

        self._thread.started.connect(self._worker.initialize)
        self._preview_requested.connect(self._worker.preview)
        self._commit_requested.connect(self._worker.commit)
        self._discard_requested.connect(self._worker.discard)
        self._shutdown_requested.connect(self._worker.shutdown)
        self._worker.progress.connect(self.progress)
        self._worker.preview_finished.connect(self._on_preview_finished)
        self._worker.generation_finished.connect(self._on_generation_finished)
        self._worker.shutdown_ready.connect(self._thread.quit)
        self._thread.finished.connect(self._on_stopped)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    @property
    def is_busy(self) -> bool:
        return self._busy

    def _start(self, task_id: str) -> CancellationToken:
        if self._busy or self._shutdown_pending:
            raise RuntimeError("已有任务正在执行。")
        self._busy = True
        self._task_id = task_id
        self._token = CancellationToken()
        return self._token

    def start_preview(self, task_id: str, config_path: str, baseline_path: str) -> None:
        token = self._start(task_id)
        self._preview_requested.emit(task_id, config_path, baseline_path, token)

    def start_commit(self, task_id: str, session_id: str, output_path: str) -> None:
        token = self._start(task_id)
        self._commit_requested.emit(task_id, session_id, output_path, token)

    def discard(self, session_id: str) -> None:
        if session_id and not self._shutdown_pending:
            self._discard_requested.emit(session_id)

    def cancel_current(self) -> None:
        """直接设置线程安全令牌，不依赖被重任务占用的事件循环。"""
        if self._token is not None:
            self._token.cancel()

    def shutdown(self) -> None:
        if self._shutdown_pending:
            return
        self._shutdown_pending = True
        if self._busy:
            self.cancel_current()
        else:
            self._shutdown_requested.emit()

    @Slot(str, object)
    def _on_preview_finished(self, task_id: str, result: object) -> None:
        if task_id != self._task_id:
            return
        self._finish_task()
        self.preview_finished.emit(result)

    @Slot(str, object)
    def _on_generation_finished(self, task_id: str, result: object) -> None:
        if task_id != self._task_id:
            return
        self._finish_task()
        self.generation_finished.emit(result)

    def _finish_task(self) -> None:
        self._busy = False
        self._token = None
        if self._shutdown_pending:
            self._shutdown_requested.emit()

    @Slot()
    def _on_stopped(self) -> None:
        LOGGER.info("GUI 工作线程已停止")
        self.stopped.emit()
