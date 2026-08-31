"""把公共 DTO 映射为单窗口可绑定状态。"""

from __future__ import annotations

import logging
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Slot

from davinci_gw.contracts import (
    CheckResultDto,
    FeatureSummaryDto,
    GenerationResultDto,
    InputFileDto,
    IssueDto,
    OperationStatus,
    PreparedSessionDto,
    ProgressEventDto,
)

from .output_paths import suggest_output_path, validate_output_path
from .state import ControlState, GuiState, controls_for
from .task_runner import QtTaskRunner

LOGGER = logging.getLogger("davinci_gw.gui")


class GatewayViewModel(QObject):
    """GUI 的唯一业务状态源；控件不直接调用 Facade。"""

    changed = Signal()
    issues_changed = Signal(object)
    preview_changed = Signal()
    shutdown_finished = Signal()

    def __init__(self, runner: QtTaskRunner, parent=None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.state = GuiState.EMPTY
        self.config_path = ""
        self.baseline_path = ""
        self.output_path = ""
        self.output_error = ""
        self.output_manually_edited = False
        self.session_id: str | None = None
        self.operation_id = ""
        self.target_version: str | None = None
        self.features: tuple[FeatureSummaryDto, ...] = ()
        self.issues: tuple[IssueDto, ...] = ()
        self.checks: tuple[CheckResultDto, ...] = ()
        self.input_files: tuple[InputFileDto, ...] = ()
        self.artifact_path = ""
        self.stage_name = "等待选择输入文件"
        self.progress_percent = 0
        self.progress_cancellable = False
        self.cancelling = False
        self._operation_started_at: float | None = None

        runner.progress.connect(self._on_progress)
        runner.preview_finished.connect(self._on_preview_finished)
        runner.generation_finished.connect(self._on_generation_finished)
        runner.stopped.connect(self.shutdown_finished)

    @property
    def inputs_valid(self) -> bool:
        return self._valid_file(self.config_path, ".xlsx") and self._valid_file(self.baseline_path, ".arxml")

    @property
    def controls(self) -> ControlState:
        return controls_for(
            self.state,
            inputs_valid=self.inputs_valid,
            cancellable=self.progress_cancellable and not self.cancelling,
        )

    @staticmethod
    def _valid_file(path: str, suffix: str) -> bool:
        candidate = Path(path).expanduser()
        return bool(path) and candidate.is_file() and candidate.suffix.lower() == suffix

    def set_config_path(self, path: str) -> None:
        self._set_input("config", path)

    def set_baseline_path(self, path: str) -> None:
        self._set_input("baseline", path)

    def _set_input(self, role: str, path: str) -> None:
        if not self.controls.inputs_enabled:
            return
        normalized = str(Path(path).expanduser()) if path else ""
        attribute = "config_path" if role == "config" else "baseline_path"
        if getattr(self, attribute) == normalized:
            return
        old_session = self.session_id
        setattr(self, attribute, normalized)
        self._clear_preview()
        if old_session:
            self.runner.discard(old_session)
        if self.baseline_path and not self.output_manually_edited:
            self.output_path = suggest_output_path(self.baseline_path)
        self.state = GuiState.READY if self.inputs_valid else GuiState.EMPTY
        self.stage_name = "输入已就绪，可以预览" if self.inputs_valid else "请选择有效的配置表和基准 ARXML"
        self.changed.emit()

    def set_output_path(self, path: str, *, manual: bool = True) -> None:
        if not self.controls.inputs_enabled:
            return
        self.output_path = path
        if manual:
            self.output_manually_edited = True
        self.output_error = ""
        self.changed.emit()

    def _clear_preview(self) -> None:
        self.session_id = None
        self.operation_id = ""
        self.target_version = None
        self.features = ()
        self.issues = ()
        self.checks = ()
        self.input_files = ()
        self.artifact_path = ""
        self.output_error = ""
        self.issues_changed.emit(self.issues)
        self.preview_changed.emit()

    def start_preview(self) -> None:
        if not self.controls.preview_enabled:
            return
        old_session = self.session_id
        self._clear_preview()
        if old_session:
            self.runner.discard(old_session)
        self.state = GuiState.PREPARING
        self.stage_name = "准备预览"
        self.progress_percent = 0
        self.progress_cancellable = True
        self.cancelling = False
        task_id = str(uuid4())
        self._operation_started_at = monotonic()
        LOGGER.info("开始预览 task=%s", task_id)
        self.runner.start_preview(task_id, self.config_path, self.baseline_path)
        self.changed.emit()

    def start_generate(self) -> None:
        if not self.controls.generate_enabled or not self.session_id:
            return
        error = validate_output_path(self.output_path, self.baseline_path)
        if error:
            self.output_error = error
            self.changed.emit()
            return
        self.output_error = ""
        self.state = GuiState.GENERATING
        self.stage_name = "准备生成"
        self.progress_percent = 0
        self.progress_cancellable = True
        self.cancelling = False
        task_id = str(uuid4())
        self._operation_started_at = monotonic()
        LOGGER.info("开始生成 task=%s", task_id)
        self.runner.start_commit(task_id, self.session_id, self.output_path)
        self.changed.emit()

    def cancel(self) -> None:
        if not self.controls.cancel_enabled:
            return
        self.cancelling = True
        self.stage_name = "正在等待安全取消点…"
        self.runner.cancel_current()
        self.changed.emit()

    def shutdown(self) -> None:
        self.runner.shutdown()

    @Slot(object)
    def _on_progress(self, event: ProgressEventDto) -> None:
        self.operation_id = event.operation_id
        self.stage_name = event.stage_name
        self.progress_percent = max(0, min(100, round(event.percent)))
        self.progress_cancellable = event.cancellable
        LOGGER.info(
            "进度 operation=%s stage=%s percent=%s cancellable=%s",
            event.operation_id, event.stage_id, self.progress_percent, event.cancellable,
        )
        self.changed.emit()

    @Slot(object)
    def _on_preview_finished(self, result: PreparedSessionDto) -> None:
        self.operation_id = result.operation_id
        preview = result.preview
        if preview is not None:
            self.target_version = preview.target_version
            self.features = preview.features
            self.issues = preview.issues
            self.checks = preview.checks
            self.input_files = preview.input_files
        else:
            self.issues = result.issues
        self.session_id = result.session_id
        self.progress_cancellable = False
        self.cancelling = False
        if result.status is OperationStatus.SUCCESS and result.session_id:
            self.state = GuiState.PREVIEW_VALID
            self.stage_name = "预览通过，可以生成"
            if not self.output_manually_edited:
                self.output_path = suggest_output_path(self.baseline_path, self.target_version)
        elif result.status is OperationStatus.CANCELLED:
            self.state = GuiState.CANCELLED
            self.stage_name = "预览已取消"
        elif result.status is OperationStatus.INTERNAL_FAILURE:
            self.state = GuiState.FAILED
            self.stage_name = "预览失败，请查看本地日志"
        else:
            self.state = GuiState.PREVIEW_INVALID
            self.stage_name = "预览未通过，请处理问题后重试"
        elapsed = monotonic() - self._operation_started_at if self._operation_started_at is not None else 0.0
        self._operation_started_at = None
        LOGGER.info("预览结束 status=%s elapsed=%.3fs", result.status.value, elapsed)
        self.issues_changed.emit(self.issues)
        self.preview_changed.emit()
        self.changed.emit()

    @Slot(object)
    def _on_generation_finished(self, result: GenerationResultDto) -> None:
        self.operation_id = result.operation_id
        self.session_id = None
        self.progress_cancellable = False
        self.cancelling = False
        if result.issues:
            self.issues = result.issues
            self.issues_changed.emit(self.issues)
        if result.status is OperationStatus.SUCCESS and result.artifact:
            self.state = GuiState.SUCCESS
            self.artifact_path = result.artifact.path
            self.stage_name = "生成完成"
            self.progress_percent = 100
        elif result.status is OperationStatus.CANCELLED:
            self.state = GuiState.CANCELLED
            self.stage_name = "生成已安全取消，未发布目标文件"
        else:
            self.state = GuiState.FAILED
            self.stage_name = "生成失败，未报告成功"
        elapsed = monotonic() - self._operation_started_at if self._operation_started_at is not None else 0.0
        self._operation_started_at = None
        LOGGER.info("生成结束 status=%s elapsed=%.3fs", result.status.value, elapsed)
        self.changed.emit()
