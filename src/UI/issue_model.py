"""问题表模型、筛选、自定义富单元格绘制和复制文本。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from davinci_gw.contracts import IssueDto

from .styles import LIGHT, THEMES

SEVERITY_COLORS = {
    "ERROR": "#d64545",
    "WARNING": "#c2820a",
    "DECISION": "#7c3aed",
    "INFO": "#2563eb",
}

# 级别色必须随主题走：写死的浅色值在夜间主题下与级别徽标不是同一个色，
# 同一屏里两处颜色对不上。这里给出主题 token，运行时按当前主题取色。
SEVERITY_COLOR_TOKENS = {
    "ERROR": "danger_text",
    "WARNING": "warning_text",
    "DECISION": "purple_text",
    "INFO": "accent",
}


def severity_color(severity: str, theme: str = "light") -> str:
    """按主题给出级别文字颜色。"""
    token = SEVERITY_COLOR_TOKENS.get(severity.upper())
    if token is None:
        return SEVERITY_COLORS.get(severity.upper(), "#d64545")
    colors = THEMES.get(theme, LIGHT)
    return colors.get(token, SEVERITY_COLORS.get(severity.upper(), "#d64545"))

SEVERITY_LABELS = {
    "ERROR": "错误",
    "WARNING": "警告",
    "DECISION": "保留决定",
    "INFO": "提示",
}

# 问题表行高：描述最多两行、工程建议最多三行，需要 108 像素才放得下。
# 表格的表头行高与自绘委托共用这个值，避免两处漂移导致内容被裁剪。
ISSUE_ROW_HEIGHT = 108


class IssueTableModel(QAbstractTableModel):
    """以稳定列顺序展示公共 IssueDto。"""

    HEADERS = ("级别", "代码", "报文 ID", "报文名称", "说明", "位置", "处理建议", "确认")
    ACK_COLUMN = 7
    ACK_SEVERITIES = ("WARNING", "DECISION")

    acknowledgement_changed = Signal()
    """人工确认状态变化时发出；界面据此更新生成门禁。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._issues: tuple[IssueDto, ...] = ()
        self._acknowledged: set[int] = set()
        self._theme = "light"

    def set_theme(self, theme: str) -> None:
        """级别文字颜色随主题变化，切换后需要重画。"""
        self._theme = theme
        if self._issues:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._issues) - 1, 0),
                [Qt.ForegroundRole],
            )

    def set_issues(self, issues: tuple[IssueDto, ...]) -> None:
        self.beginResetModel()
        self._issues = issues
        self._acknowledged.clear()
        self.endResetModel()
        self.acknowledgement_changed.emit()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._issues)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def acknowledgeable(self, row: int) -> bool:
        """该行是否需要人工确认（警告与保留决定）。"""
        if not 0 <= row < len(self._issues):
            return False
        return self._issues[row].severity.upper() in self.ACK_SEVERITIES

    def issue_at(self, row: int) -> IssueDto | None:
        """按行取问题对象。"""
        if not 0 <= row < len(self._issues):
            return None
        return self._issues[row]

    def acknowledged(self, row: int) -> bool:
        return row in self._acknowledged

    def acknowledge_all(self, rows) -> int:
        """批量确认给定行；返回实际确认的数量。"""
        changed = 0
        for row in rows:
            if self.acknowledgeable(row) and row not in self._acknowledged:
                self._acknowledged.add(row)
                changed += 1
        if changed and self._issues:
            top = self.index(0, 0)
            bottom = self.index(len(self._issues) - 1, self.ACK_COLUMN)
            self.dataChanged.emit(top, bottom)
            self.acknowledgement_changed.emit()
        return changed

    def pending_count(self, rows=None) -> int:
        """统计仍需人工确认的行数。"""
        candidates = range(len(self._issues)) if rows is None else rows
        return sum(1 for row in candidates if self.acknowledgeable(row) and row not in self._acknowledged)

    @staticmethod
    def _location(issue: IssueDto) -> str:
        parts = []
        if issue.sheet_name:
            parts.append(issue.sheet_name)
        if issue.row_number is not None:
            parts.append(f"第 {issue.row_number} 行")
        if issue.field_name:
            parts.append(issue.field_name)
        if parts:
            return " / ".join(parts)
        if issue.file_path:
            # 只显示文件名，完整路径由双击详情弹窗给出，避免整条绝对路径占满整列。
            return Path(issue.file_path).name
        return "—"

    @staticmethod
    def _advice(issue: IssueDto) -> str:
        if issue.severity.upper() in {"INFO", "DECISION"}:
            if isinstance(issue.actual_value, str) and issue.actual_value.strip():
                return issue.actual_value.strip()
            return "无需处理"
        if issue.code == "PDUR_ROUTING_GROUP_NON_MAIN_MEMBER":
            return "请核对基线 ARXML 中该路由组成员的实际归属；工具不会自动迁移"
        if issue.code == "PDUR_ROUTING_GROUP_NON_CANIF_MEMBERS_IGNORED":
            return "无需修改配置表；工具已排除该成员，如需确认请核对基线 ARXML"
        if issue.code == "PDUR_ROUTING_GROUP_SECONDARY_GROUP_IGNORED":
            return "无需修改配置表；专项组保持不变，普通新增使用已确认的应用组"
        if issue.code == "DIRECT_SOURCE_TYPE_FROM_BASELINE":
            return "无需处理；工具采用完整既有源 Rx 的基准类型且不会修改该对象"
        if issue.code in {"DIRECT_DELETE_NOT_FOUND", "SIGNAL_DELETE_NOT_FOUND"}:
            return "无需处理；目标路由已经不存在，本次按幂等语义跳过"
        if isinstance(issue.actual_value, str) and issue.actual_value.strip():
            return issue.actual_value.strip()
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

    def flags(self, index):
        if index.isValid() and index.column() == self.ACK_COLUMN and self.acknowledgeable(index.row()):
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        return super().flags(index)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._issues):
            return None
        issue = self._issues[index.row()]
        if index.column() == self.ACK_COLUMN:
            if role == Qt.CheckStateRole:
                return Qt.Checked if self.acknowledged(index.row()) else Qt.Unchecked
            if role == Qt.DisplayRole:
                return ""
            if role == Qt.ToolTipRole:
                return "点击切换人工确认状态"
            return None
        values = self._values(issue)
        if role in {Qt.DisplayRole, Qt.ToolTipRole}:
            return values[index.column()]
        if role == Qt.UserRole:
            return issue.severity.upper()
        if role == Qt.ForegroundRole and index.column() == 0:
            color = severity_color(issue.severity, self._theme)
            return QBrush(QColor(color)) if color else None
        return None

    def setData(self, index, value, role=Qt.DisplayRole):  # noqa: N802
        if not index.isValid() or index.column() != self.ACK_COLUMN:
            return False
        if role == Qt.CheckStateRole:
            if not self.acknowledgeable(index.row()):
                return False
            if Qt.CheckState(value) == Qt.Checked:
                self._acknowledged.add(index.row())
            else:
                self._acknowledged.discard(index.row())
            left = self.index(index.row(), 0)
            right = self.index(index.row(), self.ACK_COLUMN)
            self.dataChanged.emit(left, right)
            self.acknowledgement_changed.emit()
            return True
        return False

    def copy_text(self, source_rows: set[int]) -> str:
        """把所选源行转换为适合粘贴到工单的文本。"""
        tab = chr(9)
        newline = chr(10)
        lines = []
        for row in sorted(source_rows):
            issue = self._issues[row]
            lines.append(tab.join(self._values(issue)))
        return newline.join(lines)


class IssueFilterProxyModel(QSortFilterProxyModel):
    """同时按级别和全文关键字筛选问题，并为工作台表格提供现代列标题。"""

    VIEW_HEADERS = {
        0: "☐    级别",
        1: "规则代码",
        4: "问题描述与工程建议",
        5: "ARXML 节点 / 矩阵位置",
        7: "确认状态",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._severity = "ALL"
        self._search = ""
        self.setDynamicSortFilter(True)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole and section in self.VIEW_HEADERS:
                return self.VIEW_HEADERS[section]
            if role == Qt.TextAlignmentRole:
                if section == IssueTableModel.ACK_COLUMN:
                    return int(Qt.AlignRight | Qt.AlignVCenter)
                return int(Qt.AlignLeft | Qt.AlignVCenter)
        return super().headerData(section, orientation, role)

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


class IssueTableDelegate(QStyledItemDelegate):
    """把问题表渲染为现代工程工作台样式：彩色级别徽标、双行说明与建议、确认状态胶囊。"""

    def __init__(self, theme: str = "light", parent=None) -> None:
        super().__init__(parent)
        self._theme = theme

    def set_theme(self, theme: str) -> None:
        self._theme = theme

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        """给出满内容行高：描述两行、工程建议三行都要放得下。"""
        base = super().sizeHint(option, index)
        return QSize(base.width(), ISSUE_ROW_HEIGHT)

    @staticmethod
    def _wrap_lines(text: str, width: float, fm, max_lines: int) -> list[str]:
        """按当前字体度量把文本折行；绘制与行高测量共用同一套规则。"""
        if not text:
            return []
        lines: list[str] = []
        current = ""
        current_width = 0.0
        for char in text:
            char_width = fm.horizontalAdvance(char)
            if current and current_width + char_width > width:
                lines.append(current)
                current = ""
                current_width = 0.0
                if len(lines) == max_lines:
                    break
            current += char
            current_width += char_width
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and len("".join(lines)) < len(text):
            lines[-1] = fm.elidedText(lines[-1] + "…", Qt.ElideRight, int(width))
        return lines

    @staticmethod
    def _draw_wrapped(painter: QPainter, text: str, rect: QRectF, max_lines: int) -> None:
        """在限定行数内绘制换行文本，超出的部分用省略号收尾。"""
        if not text:
            return
        fm = painter.fontMetrics()
        width = max(1.0, rect.width())
        lines = IssueTableDelegate._wrap_lines(text, width, fm, max_lines)
        line_height = fm.lineSpacing()
        for idx, line in enumerate(lines):
            painter.drawText(
                QRectF(rect.left(), rect.top() + idx * line_height, rect.width(), line_height),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )

    @staticmethod
    def _source_row_and_model(index) -> tuple[int, IssueTableModel | None]:
        model = index.model()
        if isinstance(model, QSortFilterProxyModel):
            src_idx = model.mapToSource(index)
            src_model = model.sourceModel()
            if isinstance(src_model, IssueTableModel):
                return src_idx.row(), src_model
        if isinstance(model, IssueTableModel):
            return index.row(), model
        return -1, None

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        row, model = self._source_row_and_model(index)
        if model is None or row < 0:
            super().paint(painter, option, index)
            return
        issue = model.issue_at(row)
        if issue is None:
            super().paint(painter, option, index)
            return

        c = THEMES.get(self._theme, LIGHT)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        bg_color = QColor(c["table_selected"] if selected else c["surface"])
        painter.fillRect(rect, bg_color)

        # 底部分隔线
        painter.setPen(QPen(QColor(c["table_line"]), 1.0))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        col = index.column()
        sev = issue.severity.upper()
        can_ack = model.acknowledgeable(row)
        is_acked = model.acknowledged(row)

        if col == 0:
            # 左侧复选框 + 彩色级别胶囊
            cb_rect = QRectF(rect.left() + 12, rect.top() + 16, 14, 14)
            painter.setPen(QPen(QColor(c["accent"] if (can_ack and is_acked) else c["border_strong"]), 1.2))
            painter.setBrush(QColor(c["accent"]) if (can_ack and is_acked) else QColor(c["surface"]))
            painter.drawRoundedRect(cb_rect, 3.5, 3.5)
            if can_ack and is_acked:
                painter.setPen(QPen(QColor("#ffffff"), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawLine(
                    QPointF(cb_rect.left() + 3.0, cb_rect.top() + 7.2),
                    QPointF(cb_rect.left() + 6.0, cb_rect.top() + 10.2),
                )
                painter.drawLine(
                    QPointF(cb_rect.left() + 6.0, cb_rect.top() + 10.2),
                    QPointF(cb_rect.left() + 11.2, cb_rect.top() + 4.2),
                )

            label = SEVERITY_LABELS.get(sev, sev)
            badge_styles = {
                "ERROR": (c["danger_soft"], c["danger_border"], c["danger_text"]),
                "WARNING": (c["warning_soft"], c["warning_border"], c["warning_text"]),
                "DECISION": (c["purple_soft"], c["purple_border"], c["purple_text"]),
                "INFO": (c["accent_soft"], c["accent_muted"], c["accent"]),
            }
            b_bg, b_border, b_fg = badge_styles.get(sev, (c["chip_bg"], c["border"], c["chip_fg"]))
            font = QFont(option.font)
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()
            bw = max(38, fm.horizontalAdvance(label) + 16)
            badge_rect = QRectF(rect.left() + 36, rect.top() + 12, bw, 22)
            painter.setPen(QPen(QColor(b_border), 1.0))
            painter.setBrush(QColor(b_bg))
            painter.drawRoundedRect(badge_rect, 5.0, 5.0)
            painter.setPen(QColor(b_fg))
            painter.drawText(badge_rect, Qt.AlignCenter, label)

        elif col == 1:
            # 规则代码（等宽粗体）
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            font.setPixelSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(c["text"]))
            code_rect = QRectF(rect.left() + 10, rect.top() + 12, rect.width() - 16, 20)
            elided = painter.fontMetrics().elidedText(issue.code, Qt.ElideRight, int(code_rect.width()))
            painter.drawText(code_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)

            msg_name = model._messages(issue, "name")
            if msg_name != "—":
                sub_font = QFont(option.font)
                sub_font.setPixelSize(11)
                painter.setFont(sub_font)
                painter.setPen(QColor(c["text_faint"]))
                sub_rect = QRectF(rect.left() + 10, rect.top() + 34, rect.width() - 16, 18)
                sub_elided = painter.fontMetrics().elidedText(msg_name, Qt.ElideRight, int(sub_rect.width()))
                painter.drawText(sub_rect, Qt.AlignLeft | Qt.AlignVCenter, sub_elided)

        elif col == 4:
            # 双行：第一行粗体问题描述（可换行两行），第二行柔和工程建议（可换行两行）
            title_font = QFont(option.font)
            title_font.setPixelSize(13)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.setPen(QColor(c["text"]))
            self._draw_wrapped(
                painter, issue.message,
                QRectF(rect.left() + 10, rect.top() + 8, rect.width() - 20, 38), 2,
            )

            advice_font = QFont(option.font)
            advice_font.setPixelSize(12)
            painter.setFont(advice_font)
            painter.setPen(QColor(c["text_muted"]))
            advice = model._advice(issue)
            can_id = model._messages(issue, "can_id")
            if can_id != "—":
                advice = f"{advice}（{can_id}）"
            self._draw_wrapped(
                painter, advice,
                QRectF(rect.left() + 10, rect.top() + 50, rect.width() - 20, 50), 3,
            )

        elif col == 5:
            # ARXML 节点 / 矩阵位置
            loc_font = QFont("Cascadia Mono")
            loc_font.setStyleHint(QFont.Monospace)
            loc_font.setPixelSize(12)
            painter.setFont(loc_font)
            painter.setPen(QColor(c["text_secondary"]))
            loc_text = model._location(issue)
            loc_rect = QRectF(rect.left() + 10, rect.top() + 11, rect.width() - 16, rect.height() - 20)
            painter.drawText(loc_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, loc_text)

        elif col == IssueTableModel.ACK_COLUMN:
            # 右侧确认状态胶囊
            font = QFont(option.font)
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)

            if sev == "ERROR":
                pill_text = "✕ 需修复"
                p_bg, p_border, p_fg = c["danger_soft"], c["danger_border"], c["danger_text"]
            elif not can_ack or is_acked:
                pill_text = "✓ 已确认"
                p_bg, p_border, p_fg = c["success_soft"], c["success_border"], c["success_text"]
            else:
                pill_text = "○ 待确认"
                p_bg, p_border, p_fg = c["warning_soft"], c["warning_border"], c["warning_text"]

            pill_w = 72
            pill_h = 24
            pill_rect = QRectF(rect.right() - pill_w - 12, rect.top() + 11, pill_w, pill_h)
            painter.setPen(QPen(QColor(p_border), 1.0))
            painter.setBrush(QColor(p_bg))
            painter.drawRoundedRect(pill_rect, 6.0, 6.0)
            painter.setPen(QColor(p_fg))
            painter.drawText(pill_rect, Qt.AlignCenter, pill_text)
        else:
            super().paint(painter, option, index)

        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            row, src_model = self._source_row_and_model(index)
            if src_model is not None and row >= 0 and src_model.acknowledgeable(row):
                if index.column() == IssueTableModel.ACK_COLUMN or (
                    index.column() == 0 and event.pos().x() <= option.rect.left() + 34
                ):
                    ack_idx = src_model.index(row, IssueTableModel.ACK_COLUMN)
                    new_state = Qt.Unchecked if src_model.acknowledged(row) else Qt.Checked
                    return src_model.setData(ack_idx, new_state, Qt.CheckStateRole)
        return super().editorEvent(event, model, option, index)
