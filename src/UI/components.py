"""桌面端可复用的展示组件。

这些组件只负责外观与排版，不持有业务状态；业务状态仍由 ViewModel 统一提供。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .styles import LIGHT, THEME_LABELS, THEMES

# 筛选胶囊的圆点颜色用主题 token，而不是写死的色值：
# 写死浅色值时，夜间主题下的圆点与问题表里的级别徽标不是一个颜色。
FILTER_ITEMS = (
    ("全部", "ALL", None),
    ("错误", "ERROR", "danger_text"),
    ("警告", "WARNING", "warning_text"),
    ("提示", "INFO", "accent"),
    ("保留决定", "DECISION", "purple_text"),
)


def make_icon(kind: str, color: str, size: int = 16) -> QIcon:
    """用 QPainter 绘制清晰的矢量小图标，避免依赖外部图片资源。"""
    px = QPixmap(size * 2, size * 2)
    px.setDevicePixelRatio(2.0)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    if kind == "bolt":
        path = QPainterPath()
        path.moveTo(9.2, 2.0)
        path.lineTo(4.2, 8.8)
        path.lineTo(8.0, 8.8)
        path.lineTo(6.8, 14.0)
        path.lineTo(11.8, 7.2)
        path.lineTo(8.0, 7.2)
        path.closeSubpath()
        p.setBrush(QColor(color))
        p.drawPath(path)
    elif kind == "sun":
        p.drawEllipse(QPointF(8, 8), 3.0, 3.0)
        for dx, dy in ((0, -5.5), (0, 5.5), (-5.5, 0), (5.5, 0), (-3.9, -3.9), (3.9, -3.9), (-3.9, 3.9), (3.9, 3.9)):
            p.drawLine(QPointF(8 + dx * 0.72, 8 + dy * 0.72), QPointF(8 + dx, 8 + dy))
    elif kind == "moon":
        path = QPainterPath()
        path.addEllipse(QRectF(3.5, 3.5, 9.0, 9.0))
        cut = QPainterPath()
        cut.addEllipse(QRectF(6.2, 2.2, 8.5, 8.5))
        crescent = path.subtracted(cut)
        p.setBrush(QColor(color))
        p.drawPath(crescent)
    elif kind == "eye":
        path = QPainterPath()
        path.moveTo(1.8, 8.0)
        path.quadTo(8.0, 2.8, 14.2, 8.0)
        path.quadTo(8.0, 13.2, 1.8, 8.0)
        p.drawPath(path)
        p.drawEllipse(QPointF(8.0, 8.0), 2.0, 2.0)
    elif kind == "play":
        path = QPainterPath()
        path.moveTo(4.5, 3.2)
        path.lineTo(12.5, 8.0)
        path.lineTo(4.5, 12.8)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "search":
        p.drawEllipse(QPointF(7.0, 7.0), 4.2, 4.2)
        p.drawLine(QPointF(10.2, 10.2), QPointF(13.5, 13.5))
    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(2.0, 4.5)
        path.lineTo(6.0, 4.5)
        path.lineTo(7.4, 6.0)
        path.lineTo(14.0, 6.0)
        path.lineTo(14.0, 12.5)
        path.lineTo(2.0, 12.5)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "upload":
        p.drawLine(QPointF(8.0, 11.0), QPointF(8.0, 3.5))
        p.drawLine(QPointF(5.2, 6.2), QPointF(8.0, 3.5))
        p.drawLine(QPointF(10.8, 6.2), QPointF(8.0, 3.5))
        p.drawLine(QPointF(3.5, 13.0), QPointF(12.5, 13.0))
    elif kind == "pulse":
        path = QPainterPath()
        path.moveTo(1.5, 8.5)
        path.lineTo(4.5, 8.5)
        path.lineTo(6.5, 3.5)
        path.lineTo(9.2, 12.5)
        path.lineTo(11.2, 8.5)
        path.lineTo(14.5, 8.5)
        p.drawPath(path)
    p.end()
    return QIcon(px)


class BrandLogoWidget(QWidget):
    """页头左上角的品牌网关拓扑图标。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(38, 38)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        grad = QLinearGradient(0, 0, 38, 38)
        grad.setColorAt(0.0, QColor("#2563eb"))
        grad.setColorAt(1.0, QColor("#1d4ed8"))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(1, 1, 36, 36), 10, 10)

        pen = QPen(QColor("#ffffff"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # 顶部分支节点到底部汇聚节点的拓扑线
        p.drawLine(QPointF(13, 15), QPointF(13, 19))
        p.drawLine(QPointF(25, 15), QPointF(25, 19))
        p.drawLine(QPointF(13, 19), QPointF(25, 19))
        p.drawLine(QPointF(19, 19), QPointF(19, 24))
        p.drawRoundedRect(QRectF(9.5, 9.5, 7, 5.5), 1.5, 1.5)
        p.drawRoundedRect(QRectF(21.5, 9.5, 7, 5.5), 1.5, 1.5)
        p.drawRoundedRect(QRectF(15.5, 24.0, 7, 5.5), 1.5, 1.5)
        p.end()


class FileSlotIconWidget(QWidget):
    """文件槽左侧的矢量文档图标（表格 / ARXML 代码）。"""

    def __init__(self, variant: str, parent=None) -> None:
        super().__init__(parent)
        self._variant = variant
        self._theme = "light"
        self.setFixedSize(36, 36)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        c = THEMES.get(self._theme, LIGHT)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._variant == "xlsx":
            bg = QColor(c["xlsx_icon_bg"])
            border = QColor(c["xlsx_icon_border"])
            fg = QColor(c["xlsx_icon_fg"])
        else:
            bg = QColor(c["arxml_icon_bg"])
            border = QColor(c["arxml_icon_border"])
            fg = QColor(c["arxml_icon_fg"])

        p.setPen(QPen(border, 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, 35, 35), 9, 9)

        pen = QPen(fg, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # 文档外框与折角
        doc = QPainterPath()
        doc.moveTo(11, 8)
        doc.lineTo(21, 8)
        doc.lineTo(25, 12)
        doc.lineTo(25, 28)
        doc.lineTo(11, 28)
        doc.closeSubpath()
        p.drawPath(doc)
        p.drawLine(QPointF(20.5, 8.5), QPointF(20.5, 12.5))
        p.drawLine(QPointF(20.5, 12.5), QPointF(24.5, 12.5))

        if self._variant == "xlsx":
            # 表格横线与竖线
            p.drawLine(QPointF(14, 16), QPointF(22, 16))
            p.drawLine(QPointF(14, 20), QPointF(22, 20))
            p.drawLine(QPointF(14, 24), QPointF(22, 24))
            p.drawLine(QPointF(17.5, 15.5), QPointF(17.5, 24.5))
        else:
            # XML 尖括号 < >
            p.drawLine(QPointF(16.8, 17.5), QPointF(14.3, 20.5))
            p.drawLine(QPointF(14.3, 20.5), QPointF(16.8, 23.5))
            p.drawLine(QPointF(19.2, 17.5), QPointF(21.7, 20.5))
            p.drawLine(QPointF(21.7, 20.5), QPointF(19.2, 23.5))
        p.end()


class InfoIconWidget(QWidget):
    """说明框左侧的圆形 i 图标。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._theme = "light"

    def set_theme(self, theme: str) -> None:
        """图标颜色跟随主题，深色下用更亮的一档。"""
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        colors = THEMES.get(self._theme, THEMES["light"])
        color = QColor(colors.get("text_muted", "#94a3b8"))
        p.setPen(QPen(color, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(1.5, 1.5, 13.0, 13.0))
        p.drawLine(QPointF(8.0, 7.2), QPointF(8.0, 11.2))
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(8.0, 4.8), 1.0, 1.0)
        p.end()


class GridCanvasWidget(QWidget):
    """右侧校验工作台的细网格背景容器。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._theme = "light"

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        c = THEMES.get(self._theme, LIGHT)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(c["canvas"]))
        pen = QPen(QColor(c["grid_line"]), 1.0)
        p.setPen(pen)
        step = 24
        w = self.width()
        h = self.height()
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        p.end()


class FileDropEdit(QLineEdit):
    """只接收一个本地文件，并把路径交给 ViewModel 的统一入口。"""

    path_dropped = Signal(str)

    def __init__(self, suffix: str, parent=None) -> None:
        super().__init__(parent)
        self._suffix = suffix
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() == self._suffix:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self.path_dropped.emit(event.mimeData().urls()[0].toLocalFile())
        event.acceptProposedAction()


class StepBadge(QLabel):
    """步骤序号徽标。"""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("stepBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(22, 22)


class SectionCard(QFrame):
    """带标题行的白色卡片容器。

    标题行由可选的步骤徽标/图标、标题、内联副标题或右侧说明组成；内容放入 ``body`` 布局。
    """

    def __init__(
        self,
        title: str,
        *,
        step: str | None = None,
        icon_kind: str | None = None,
        hint: str = "",
        hint_badge: str = "",
        inline_hint: str = "",
        inline_icon: str | None = None,
        status_chip: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._theme = "light"
        self._icon_kind = icon_kind
        self._inline_icon = inline_icon
        self._icon_label: QLabel | None = None
        self._chip_icon_label: QLabel | None = None
        if step:
            header.addWidget(StepBadge(step))
        elif icon_kind:
            icon_lbl = QLabel()
            icon_lbl.setObjectName("sectionIcon")
            self._icon_label = icon_lbl
            header.addWidget(icon_lbl, 0, Qt.AlignVCenter)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        header.addWidget(title_label)
        if inline_hint:
            # 说明标签用胶囊呈现，和右侧纯文字说明区分开。
            chip = QFrame()
            chip.setObjectName("softChip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 3, 9, 3)
            chip_layout.setSpacing(5)
            if inline_icon:
                chip_icon = QLabel()
                self._chip_icon_label = chip_icon
                chip_layout.addWidget(chip_icon, 0, Qt.AlignVCenter)
            chip_label = QLabel(inline_hint)
            chip_label.setObjectName("softChipText")
            chip_layout.addWidget(chip_label)
            header.addWidget(chip, 0, Qt.AlignVCenter)
        header.addStretch(1)
        if hint_badge:
            badge_label = QLabel(hint_badge)
            badge_label.setObjectName("sectionHintBadge")
            header.addWidget(badge_label, 0, Qt.AlignVCenter)
        elif hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("sectionHint")
            header.addWidget(hint_label, 0, Qt.AlignVCenter)
        self.status_chip_label = QLabel(status_chip)
        self.status_chip_label.setObjectName("readyChip")
        self.status_chip_label.setVisible(bool(status_chip))
        header.addWidget(self.status_chip_label, 0, Qt.AlignVCenter)
        outer.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)
        self.set_theme(self._theme)

    def set_theme(self, theme: str) -> None:
        """标题图标与胶囊图标跟随主题，避免夜间主题下仍是浅色的一档。"""
        self._theme = theme
        colors = THEMES.get(theme, THEMES["light"])
        if self._icon_label is not None and self._icon_kind:
            self._icon_label.setPixmap(
                make_icon(self._icon_kind, colors["accent"], 16).pixmap(QSize(16, 16))
            )
        if self._chip_icon_label is not None and self._inline_icon:
            self._chip_icon_label.setPixmap(
                make_icon(self._inline_icon, colors["text_muted"], 13).pixmap(QSize(13, 13))
            )


class ElidedLabel(QLabel):
    """按可用宽度省略的标签。

    普通 QLabel 的最小宽度就是整段文字的宽度：窗口窄下来时，
    它会把整行撑宽，右栏因此出现横向滚动条，操作按钮被推出可视区。
    这里让标签可以缩到 0 宽，被截断的部分用省略号表示。
    """

    def __init__(self, text: str = "", parent=None, minimum_width: int = 0) -> None:
        super().__init__(parent)
        self._full_text = text
        self._minimum_width = max(int(minimum_width), 0)
        # Preferred 而不是 Ignored：Ignored 会让布局连首选宽度都不给，
        # 标题会被压成 0 宽而整条消失。Preferred 仍然按原文宽度排，
        # 空间不够时才缩到最小宽度 0 并用省略号表示截断。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        super().setText(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小宽度：不让整段文字撑宽所在行。

        QLabel 的最小宽度就是文字本身的宽度，窄窗口下会把整行撑宽，
        右栏因此出现横向滚动条。这里让行可以收缩，截断部分用省略号表示。
        `minimum_width` 给一个下限，免得标签被挤成只剩一个省略号。
        """
        base = super().minimumSizeHint()
        return QSize(self._minimum_width, base.height())

    def sizeHint(self) -> QSize:  # noqa: N802
        """首选宽度按**原文**算，而不是按省略后的文本算。

        省略后的文本一旦成了首选宽度，布局就会按这个更小的宽度重新分配，
        标签被永远锁在省略状态：文案变长后不再恢复，窗口拉宽也恢复不了
        （实测「等待输入」换成「预览通过，可以生成」后标题一直停在「预览通过，…」）。
        """
        base = super().sizeHint()
        metrics = self.fontMetrics()
        full = metrics.horizontalAdvance(self._full_text)
        return QSize(max(int(full), 0), base.height())

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self._refresh_elision()

    def fullText(self) -> str:  # noqa: N802
        """未经省略的原文。"""
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elision()

    def _refresh_elision(self) -> None:
        width = self.width()
        if width <= 0:
            elided = self._full_text
        else:
            elided = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        if elided != super().text():
            super().setText(elided)


class StatusBanner(QFrame):
    """状态条：左侧色条 + 状态标题与内联说明 + 右侧三步面包屑与 STEP 徽标。"""

    STEP_NAMES = ("1. 选择文件", "2. 预览校验", "3. 确认生成")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self.rail = QFrame()
        self.rail.setObjectName("statusRail")
        self.rail.setFixedSize(4, 18)
        layout.addWidget(self.rail, 0, Qt.AlignVCenter)

        self.title = ElidedLabel()
        self.title.setObjectName("statusTitle")
        layout.addWidget(self.title, 0, Qt.AlignVCenter)

        self.detail = ElidedLabel(minimum_width=96)
        self.detail.setObjectName("statusDetail")
        layout.addWidget(self.detail, 1, Qt.AlignVCenter)

        self._crumbs: list[QLabel] = []
        self._separators: list[QLabel] = []
        for idx, text in enumerate(self.STEP_NAMES):
            if idx > 0:
                sep = QLabel("/")
                sep.setObjectName("stepCrumbSep")
                layout.addWidget(sep, 0, Qt.AlignVCenter)
                self._separators.append(sep)
            crumb = QLabel(text)
            crumb.setObjectName("stepCrumb")
            crumb.setProperty("active", "false")
            layout.addWidget(crumb, 0, Qt.AlignVCenter)
            self._crumbs.append(crumb)

        layout.addSpacing(4)
        self.counter = QLabel()
        self.counter.setObjectName("stepCounter")
        layout.addWidget(self.counter, 0, Qt.AlignVCenter)
        self.set_state("info")
        self.set_message("等待输入")
        self.set_step(1, 3)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_step_counter()

    def _sync_step_counter(self) -> None:
        """宽度不够时收起 STEP 徽标，把空间让给状态标题与说明。

        徽标写的是「STEP 2 / 3」，而面包屑里高亮的那一步说的是同一件事——
        窄窗口下两者只能留一个，状态文案比进度徽标更要紧。

        需要的宽度一律按 `sizeHint` 算，不能按控件当前的几何宽度算：
        布局会把富余空间分给可伸缩的控件（实测分隔符「/」的几何宽度能到 105 像素，
        而它的自然宽度只有 6），按几何算出来的需求会随状态漂移。
        """
        margins = self.layout().contentsMargins()
        inner = max(self.width() - margins.left() - margins.right(), 0)
        fixed = self.rail.sizeHint().width()
        fixed += sum(c.sizeHint().width() for c in self._crumbs)
        fixed += sum(s.sizeHint().width() for s in self._separators)
        spacing = self.layout().spacing() * (len(self._crumbs) + len(self._separators) + 3)
        needed = fixed + spacing + self.title.sizeHint().width() + self.detail.sizeHint().width()
        counter_width = self.counter.sizeHint().width()
        # 判定只看宽度，与徽标当前可见与否无关，因此不会出现收起又弹出的抖动。
        self.counter.setVisible(inner >= needed + counter_width)

    def set_state(self, state: str) -> None:
        """切换状态配色：neutral / info / success / warning / error。"""
        for widget in (self, self.rail):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def set_message(self, title: str, detail: str = "") -> None:
        """更新状态文案。"""
        self.title.setText(title)
        self.detail.setText(f"— {detail}" if detail and not detail.startswith("—") else detail)
        self.detail.setVisible(bool(detail))
        # 文案变了但横幅宽度没变，resizeEvent 不会来，这里自己同步一次。
        self._sync_step_counter()

    def set_step(self, current: int, total: int) -> None:
        """更新流水线步骤面包屑与 STEP 指示。"""
        self.counter.setText(f"STEP {current} / {total}")
        for idx, crumb in enumerate(self._crumbs, start=1):
            crumb.setProperty("active", "true" if idx == current else "false")
            crumb.style().unpolish(crumb)
            crumb.style().polish(crumb)
        self._sync_step_counter()


class FileSlot(QFrame):
    """紧凑文件槽：矢量图标 + 标题与后缀徽标 + 可编辑路径 + 浏览/清除，支持拖入文件。"""

    path_dropped = Signal(str)
    browse_requested = Signal()
    clear_requested = Signal()

    def __init__(self, title: str, suffix: str, glyph: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("fileSlot")
        self.setAcceptDrops(True)
        self._suffix = suffix
        variant = "xlsx" if suffix == ".xlsx" else "arxml"
        self.setProperty("variant", variant)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(10)

        self.icon_widget = FileSlotIconWidget(variant)
        layout.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("fileSlotTitle")
        suffix_chip = QLabel(suffix)
        suffix_chip.setObjectName("fileSlotSuffix")
        suffix_chip.setProperty("variant", variant)
        self.ready_chip = QLabel("已就绪")
        self.ready_chip.setObjectName("readyChip")
        self.ready_chip.setVisible(False)
        head.addWidget(title_label)
        head.addWidget(suffix_chip)
        head.addWidget(self.ready_chip)
        head.addStretch(1)
        text_box.addLayout(head)

        self.edit = FileDropEdit(suffix)
        self.edit.setObjectName("fileSlotPath")
        self.edit.setPlaceholderText("选择或拖入文件")
        text_box.addWidget(self.edit)
        layout.addLayout(text_box, 1)

        browse = QPushButton("浏览...")
        browse.setObjectName("slotBrowseBtn")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self.browse_requested)
        clear = QPushButton("清除")
        clear.setObjectName("slotClearBtn")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_requested)
        layout.addWidget(browse, 0, Qt.AlignVCenter)
        layout.addWidget(clear, 0, Qt.AlignVCenter)

    def set_ready(self, ready: bool) -> None:
        """更新就绪状态与路径提示。"""
        self.ready_chip.setVisible(False)
        if self.edit.text():
            self.edit.setToolTip(self.edit.text())
        else:
            self.edit.setToolTip("")

    def set_theme(self, theme: str) -> None:
        self.icon_widget.set_theme(theme)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        suffixes = {Path(url.toLocalFile()).suffix.lower() for url in urls if url.isLocalFile()}
        if suffixes & {self._suffix}:
            self.setProperty("dragover", "true")
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragover", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() == self._suffix:
                self.path_dropped.emit(path)
                break
        event.acceptProposedAction()


class StatusDot(QWidget):
    """筛选胶囊内的小彩色圆点。"""

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(7, 7)

    def set_color(self, color: str) -> None:
        """换色（切换主题时调用）。"""
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(0.5, 0.5, 6.0, 6.0))
        p.end()


class PillButton(QFrame):
    """带彩色圆点与计数的胶囊按钮。"""

    clicked = Signal()

    def __init__(self, label: str, dot_color: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("filterPill")
        self.setProperty("checked", "false")
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(5)
        if dot_color:
            self.dot = StatusDot(dot_color)
            layout.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.name = QLabel(label)
        self.name.setObjectName("pillName")
        self.count = QLabel("0")
        self.count.setObjectName("filterCount")
        if dot_color is None:
            self.count.setProperty("pillBadge", "true")
        layout.addWidget(self.name)
        layout.addWidget(self.count)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_checked(self, checked: bool) -> None:
        self.setProperty("checked", "true" if checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_count(self, count: int) -> None:
        self.count.setText(str(count))

    def set_dot_color(self, color: str) -> None:
        """圆点换色（切换主题时调用）。"""
        dot = getattr(self, "dot", None)
        if dot is not None:
            dot.set_color(color)


class SegmentedFilter(QFrame):
    """按级别筛选问题的胶囊分段，带实时计数。"""

    severity_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("filterPills")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._pills: dict[str, PillButton] = {}
        self._dot_tokens: dict[str, str | None] = {}
        for label, value, dot_color in FILTER_ITEMS:
            pill = PillButton(label, dot_color=dot_color)
            pill.clicked.connect(lambda value=value: self._select(value))
            layout.addWidget(pill)
            self._pills[value] = pill
            self._dot_tokens[value] = dot_color
        self._pills["ALL"].set_checked(True)
        self._value = "ALL"
        self._theme = "light"
        self.set_theme("light")

    def set_theme(self, theme: str) -> None:
        """圆点颜色跟随主题，与问题表里的级别徽标保持同一套语义色。"""
        self._theme = theme
        colors = THEMES.get(theme, THEMES["light"])
        for value, token in self._dot_tokens.items():
            if not token:
                continue
            self._pills[value].set_dot_color(colors.get(token, colors["text_muted"]))

    def _select(self, value: str) -> None:
        self._value = value
        for name, pill in self._pills.items():
            pill.set_checked(name == value)
        self.severity_changed.emit(value)

    def set_counts(self, counts: dict[str, int]) -> None:
        """更新各分类计数。"""
        for value, pill in self._pills.items():
            pill.set_count(counts.get(value, 0))

    def value(self) -> str:
        return self._value


class ThemeSegmentButton(QPushButton):
    """保留 text() 为『白天 / 夜间』以兼容既有契约，视觉上绘制『☀ 浅色 / ☾ 深色』。"""

    DISPLAY_LABELS = {"light": "浅色", "dark": "深色"}

    def __init__(self, mode: str, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._mode = mode
        self._display = self.DISPLAY_LABELS.get(mode, text)
        self.setObjectName("themeSegment")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(68, 28)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        is_dark_ui = self.parentWidget() is not None and getattr(self.parentWidget(), "_theme", "light") == "dark"
        c = THEMES["dark" if is_dark_ui else "light"]

        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self.isChecked():
            p.setPen(QPen(QColor(c["border"]), 1.0))
            p.setBrush(QColor(c["surface"]))
            p.drawRoundedRect(rect, 6.0, 6.0)
            fg = QColor(c["text"])
            icon_color = "#f59e0b" if self._mode == "light" else "#60a5fa"
        else:
            fg = QColor(c["text_muted"])
            icon_color = c["text_muted"]

        icon_px = make_icon("sun" if self._mode == "light" else "moon", icon_color, 14).pixmap(QSize(14, 14))
        p.drawPixmap(11, (self.height() - 14) // 2, icon_px)

        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.setPen(fg)
        p.drawText(QRectF(27, 0, self.width() - 31, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._display)
        p.end()


class ThemeSwitch(QFrame):
    """白天 / 夜间主题切换控件。"""

    theme_changed = Signal(str)

    def __init__(self, theme: str = "light", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("themeSwitch")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._theme = "light"
        self._buttons: dict[str, QPushButton] = {}
        for name, label in THEME_LABELS.items():
            button = ThemeSegmentButton(name, label, self)
            button.clicked.connect(lambda _checked=False, value=name: self._select(value))
            layout.addWidget(button)
            self._buttons[name] = button
        self.set_theme(theme)

    def _select(self, theme: str) -> None:
        self.set_theme(theme)
        self.theme_changed.emit(theme)

    def set_theme(self, theme: str) -> None:
        """高亮当前主题，不触发信号。"""
        self._theme = theme if theme in THEME_LABELS else "light"
        for name, button in self._buttons.items():
            button.setChecked(name == self._theme)
            button.update()
        self.update()

    def theme(self) -> str:
        return self._theme
