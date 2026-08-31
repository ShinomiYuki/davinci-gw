"""动态功能摘要的配置请求明细窗口。"""

from __future__ import annotations

from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
)

from davinci_gw.contracts import FeatureSummaryDto


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
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
