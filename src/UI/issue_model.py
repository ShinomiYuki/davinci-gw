"""问题表模型、筛选和复制文本。"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from davinci_gw.contracts import IssueDto


class IssueTableModel(QAbstractTableModel):
    """以稳定列顺序展示公共 IssueDto。"""

    HEADERS = ("级别", "代码", "报文 ID", "报文名称", "说明", "位置", "处理建议")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._issues: tuple[IssueDto, ...] = ()

    def set_issues(self, issues: tuple[IssueDto, ...]) -> None:
        self.beginResetModel()
        self._issues = issues
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._issues)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    @staticmethod
    def _location(issue: IssueDto) -> str:
        parts = []
        if issue.sheet_name:
            parts.append(issue.sheet_name)
        if issue.row_number is not None:
            parts.append(f"第 {issue.row_number} 行")
        if issue.field_name:
            parts.append(issue.field_name)
        if not parts and issue.file_path:
            parts.append(issue.file_path)
        return " / ".join(parts) or "—"

    @staticmethod
    def _advice(issue: IssueDto) -> str:
        if issue.severity.upper() in {"INFO", "DECISION"}:
            return "无需处理"
        if issue.code == "PDUR_ROUTING_GROUP_NON_MAIN_MEMBER":
            return "请核对基线 ARXML 中该路由组成员的实际归属；工具不会自动迁移"
        if issue.code == "PDUR_ROUTING_GROUP_NON_CANIF_MEMBERS_IGNORED":
            return "无需修改配置表；工具已排除该成员，如需确认请核对基线 ARXML"
        if issue.field_name:
            return f"请检查“{issue.field_name}”后重新预览"
        return "请按说明检查输入后重新预览"

    @staticmethod
    def _messages(issue: IssueDto, attribute: str) -> str:
        role_names = {"SOURCE": "源", "TARGET": "目标"}
        values = []
        for message in issue.messages:
            value = getattr(message, attribute)
            if value:
                values.append(f"{role_names.get(message.role, message.role)}：{value}")
        return " → ".join(values) or "—"

    def _values(self, issue: IssueDto) -> tuple[str, ...]:
        return (
            issue.severity,
            issue.code,
            self._messages(issue, "can_id"),
            self._messages(issue, "name"),
            issue.message,
            self._location(issue),
            self._advice(issue),
        )

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._issues):
            return None
        issue = self._issues[index.row()]
        values = self._values(issue)
        if role in {Qt.DisplayRole, Qt.ToolTipRole}:
            return values[index.column()]
        if role == Qt.UserRole:
            return issue.severity.upper()
        return None

    def copy_text(self, source_rows: set[int]) -> str:
        """把所选源行转换为适合粘贴到工单的文本。"""
        lines = []
        for row in sorted(source_rows):
            issue = self._issues[row]
            lines.append("\t".join(self._values(issue)))
        return "\n".join(lines)


class IssueFilterProxyModel(QSortFilterProxyModel):
    """同时按级别和全文关键字筛选问题。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._severity = "ALL"
        self._search = ""
        self.setDynamicSortFilter(True)

    def set_severity(self, severity: str) -> None:
        self._severity = severity.upper()
        self.invalidateFilter()

    def set_search(self, text: str) -> None:
        self._search = text.strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802
        model = self.sourceModel()
        severity = str(model.index(source_row, 0, source_parent).data(Qt.UserRole) or "")
        if self._severity != "ALL" and severity != self._severity:
            return False
        if not self._search:
            return True
        return any(
            self._search in str(model.index(source_row, column, source_parent).data() or "").casefold()
            for column in range(model.columnCount())
        )
