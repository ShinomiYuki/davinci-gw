"""独立的 OTA 检查、精确预览和确认生成窗口。"""

from __future__ import annotations

import json

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from davinci_gw.application.ota import OtaService


class _OtaTask(QThread):
    def __init__(self, operation, parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.result = {}

    def run(self) -> None:
        try:
            self.result = self.operation()
        except Exception as exc:
            self.result = {"can_generate": False, "issues": [str(exc)]}


class OtaDialog(QDialog):
    def __init__(self, baseline: str = "", parent=None, service=None) -> None:
        super().__init__(parent)
        self.service = service or OtaService()
        self._task = None
        self._preparation_id = ""
        self.setWindowTitle("OTA 白名单配置")
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        note = QLabel(
            "配置 BD、CH、DA、DK、DM、EP、GL、IC、LC、PT、SU 的 RX/TX 控制和 NM。\n"
            "工具保留所有报文组归属，组成员需手动关联。先查看完整预览，再确认生成新文件。\n"
            "关闭条件消失不会自动启动组，恢复依赖既有启用规则；实际行为需在 DaVinci 和车辆环境验证。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.baseline_edit = QLineEdit(baseline)
        self.baseline_edit.setPlaceholderText("只读基准 ARXML 绝对路径")
        self.browse_button = QPushButton("选择基准…")
        row.addWidget(self.baseline_edit)
        row.addWidget(self.browse_button)
        layout.addLayout(row)
        actions = QHBoxLayout()
        self.check_button = QPushButton("检查缺漏")
        self.preview_button = QPushButton("预览精确变更")
        self.generate_button = QPushButton("确认并生成新文件…")
        self.generate_button.setEnabled(False)
        for button in (self.check_button, self.preview_button, self.generate_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.result_view)
        self.browse_button.clicked.connect(self._browse)
        self.baseline_edit.textChanged.connect(self._invalidate)
        self.check_button.clicked.connect(lambda: self._inspect(False))
        self.preview_button.clicked.connect(lambda: self._inspect(True))
        self.generate_button.clicked.connect(self._generate)

    def _invalidate(self) -> None:
        self._preparation_id = ""
        self.generate_button.setEnabled(False)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择只读基准", self.baseline_edit.text(), "ARXML (*.arxml)")
        if path:
            self.baseline_edit.setText(path)

    def _inspect(self, preview: bool) -> None:
        self._invalidate()
        baseline = self.baseline_edit.text().strip()
        method = self.service.preview if preview else self.service.check
        self._start(lambda: method(baseline), preview)

    def _start(self, operation, is_preview: bool) -> None:
        if self._task is not None:
            return
        self._set_busy(True)
        self.result_view.setPlainText("正在处理，请等待…")
        task = _OtaTask(operation, self)
        self._task = task
        task.finished.connect(lambda: self._finished(task, is_preview))
        task.start()

    def _finished(self, task: _OtaTask, is_preview: bool) -> None:
        result = task.result
        self._task = None
        task.deleteLater()
        self.result_view.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        if is_preview and result.get("can_generate"):
            self._preparation_id = result.get("preparation_id", "")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        for control in (self.baseline_edit, self.browse_button, self.check_button, self.preview_button):
            control.setEnabled(not busy)
        self.generate_button.setEnabled(not busy and bool(self._preparation_id))

    def _generate(self) -> None:
        if not self._preparation_id or self._task is not None:
            return
        output, _ = QFileDialog.getSaveFileName(self, "选择尚不存在的新文件", "", "ARXML (*.arxml)")
        if not output:
            return
        answer = QMessageBox.question(
            self, "确认精确预览", "确认已查看当前完整预览，并按这些变更生成新 ARXML？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        preparation_id = self._preparation_id
        self._invalidate()
        self._start(lambda: self.service.generate(preparation_id, output, confirmed=True), False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._task is not None:
            event.ignore()
            return
        event.accept()

    def reject(self) -> None:
        if self._task is None:
            super().reject()
