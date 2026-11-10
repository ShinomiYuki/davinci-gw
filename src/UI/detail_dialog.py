"""动态功能摘要的配置请求明细窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
)

from davinci_gw.contracts import FeatureSummaryDto, IssueDto


class FeatureDetailDialog(QDialog):
    """展示任意 FeatureSummaryDto 携带的 ADD/DELETE 明细。"""

    HEADERS = ("操作", "类型", "源端", "目标端", "配置位置")

    def __init__(self, feature: FeatureSummaryDto, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{feature.display_name} · 变更详情")
        self.resize(920, 460)
        layout = QVBoxLayout(self)
        hint = QLabel("以下为本次计划实际新增或删除的路由；已存在、未找到和跳过项不计入。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.model = QStandardItemModel(0, len(self.HEADERS), self)
        self.model.setHorizontalHeaderLabels(self.HEADERS)
        action_labels = {"ADD": "新增", "DELETE": "删除"}
        for detail in feature.details:
            self.model.appendRow([
                QStandardItem(action_labels.get(detail.action, detail.action)),
                QStandardItem(detail.item_type),
                QStandardItem(detail.source),
                QStandardItem(detail.target),
                QStandardItem(detail.location or "—"),
            ])

        table = QTableView()
        table.setModel(self.model)
        table.setSortingEnabled(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setAccessibleName(f"{feature.display_name}变更详情")
        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText("关闭")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


class IssueDetailDialog(QDialog):
    """问题详情：位置、关联报文与处理建议，便于核对 ARXML 节点。"""

    def __init__(self, issue: IssueDto, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"问题详情 · {issue.code}")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        rows = (
            ("级别", issue.severity),
            ("代码", issue.code),
            ("类别", issue.category),
            ("说明", issue.message),
            ("文件", issue.file_path or "—"),
            ("工作表", issue.sheet_name or "—"),
            ("行号", "—" if issue.row_number is None else str(issue.row_number)),
            ("字段", issue.field_name or "—"),
            ("实际值", "—" if issue.actual_value is None else str(issue.actual_value)),
            ("关联报文", self._messages(issue)),
        )
        for caption, value in rows:
            label = QLabel(str(value))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(caption, label)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText("关闭")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    @staticmethod
    def _messages(issue: IssueDto) -> str:
        role_names = {"SOURCE": "源", "TARGET": "目标"}
        parts = []
        for message in issue.messages:
            name = message.name or "—"
            can_id = f"（{message.can_id}）" if message.can_id else ""
            parts.append(f"{role_names.get(message.role, message.role)}：{name}{can_id}")
        return "\n".join(parts) if parts else "—"
