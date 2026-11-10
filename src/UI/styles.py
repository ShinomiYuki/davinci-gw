"""桌面端的视觉常量与双主题（白天 / 夜间）。

主题只描述外观，不承载任何业务规则；控件结构由 main_window.py 负责。
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette

LIGHT = {
    "canvas": "#f8fafc",
    "grid_line": "#eef2f6",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "sidebar_bg": "#ffffff",
    "border": "#e2e8f0",
    "border_strong": "#cbd5e1",
    "text": "#0f172a",
    "text_secondary": "#334155",
    "text_muted": "#64748b",
    "text_faint": "#94a3b8",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_pressed": "#1e40af",
    "accent_soft": "#eff6ff",
    "accent_muted": "#bfdbfe",
    "success": "#10b981",
    "success_text": "#059669",
    "success_soft": "#ecfdf5",
    "success_border": "#a7f3d0",
    "warning": "#f59e0b",
    "warning_text": "#d97706",
    "warning_soft": "#fffbeb",
    "warning_border": "#fde68a",
    "danger": "#ef4444",
    "danger_text": "#e11d48",
    "danger_soft": "#fff1f2",
    "danger_border": "#fecdd3",
    "purple": "#8b5cf6",
    "purple_text": "#7c3aed",
    "purple_soft": "#f5f3ff",
    "purple_border": "#ddd6fe",
    "neutral_soft": "#f1f5f9",
    "chip_bg": "#f1f5f9",
    "chip_fg": "#475569",
    "success_chip_bg": "#ecfdf5",
    "success_chip_fg": "#059669",
    "warning_chip_bg": "#fffbeb",
    "warning_chip_fg": "#b45309",
    "input_bg": "#f8fafc",
    "button_bg": "#ffffff",
    "button_fg": "#334155",
    "button_hover": "#f8fafc",
    "button_pressed": "#f1f5f9",
    "ack_btn_bg": "#64748b",
    "ack_btn_hover": "#475569",
    "ack_btn_fg": "#ffffff",
    "disabled_bg": "#f1f5f9",
    "disabled_fg": "#94a3b8",
    "ghost_hover": "#f1f5f9",
    "table_header": "#f8fafc",
    "table_alt": "#ffffff",
    "table_line": "#f1f5f9",
    "table_selected": "#eff6ff",
    "scroll": "#cbd5e1",
    "scroll_hover": "#94a3b8",
    "tooltip_bg": "#0f172a",
    "tooltip_fg": "#ffffff",
    "on_accent": "#ffffff",
    "slot_icon_bg": "#ecfdf5",
    "slot_icon_fg": "#059669",
    "xlsx_slot_bg": "#f4fdf8",
    "xlsx_slot_border": "#86efac",
    "xlsx_icon_bg": "#dcfce7",
    "xlsx_icon_border": "#86efac",
    "xlsx_icon_fg": "#16a34a",
    "xlsx_chip_bg": "#dcfce7",
    "xlsx_chip_fg": "#15803d",
    "arxml_slot_bg": "#f6f8ff",
    "arxml_slot_border": "#c7d2fe",
    "arxml_icon_bg": "#e0e7ff",
    "arxml_icon_border": "#a5b4fc",
    "arxml_icon_fg": "#4f46e5",
    "arxml_chip_bg": "#e0e7ff",
    "arxml_chip_fg": "#4338ca",
    "callout_bg": "#f1f5f9",
    "callout_border": "#e2e8f0",
}

DARK = {
    "canvas": "#0b1120",
    "grid_line": "#131c31",
    "surface": "#0f172a",
    "surface_alt": "#1e293b",
    "sidebar_bg": "#0f172a",
    "border": "#1e293b",
    "border_strong": "#334155",
    "text": "#f1f5f9",
    "text_secondary": "#cbd5e1",
    "text_muted": "#94a3b8",
    "text_faint": "#64748b",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    "accent_pressed": "#2563eb",
    "accent_soft": "#172554",
    "accent_muted": "#1e3a8a",
    "success": "#34d399",
    "success_text": "#34d399",
    "success_soft": "#064e3b",
    "success_border": "#047857",
    "warning": "#fbbf24",
    "warning_text": "#fbbf24",
    "warning_soft": "#451a03",
    "warning_border": "#78350f",
    "danger": "#fb7185",
    "danger_text": "#fb7185",
    "danger_soft": "#4c0519",
    "danger_border": "#881337",
    "purple": "#a78bfa",
    "purple_text": "#c4b5fd",
    "purple_soft": "#2e1065",
    "purple_border": "#4c1d95",
    "neutral_soft": "#1e293b",
    "chip_bg": "#1e293b",
    "chip_fg": "#cbd5e1",
    "success_chip_bg": "#064e3b",
    "success_chip_fg": "#6ee7b7",
    "warning_chip_bg": "#451a03",
    "warning_chip_fg": "#fcd34d",
    "input_bg": "#1e293b",
    "button_bg": "#1e293b",
    "button_fg": "#e2e8f0",
    "button_hover": "#334155",
    "button_pressed": "#0f172a",
    "ack_btn_bg": "#475569",
    "ack_btn_hover": "#64748b",
    "ack_btn_fg": "#f8fafc",
    "disabled_bg": "#1e293b",
    "disabled_fg": "#475569",
    "ghost_hover": "#1e293b",
    "table_header": "#162033",
    "table_alt": "#0f172a",
    "table_line": "#1e293b",
    "table_selected": "#172554",
    "scroll": "#334155",
    "scroll_hover": "#475569",
    "tooltip_bg": "#e2e8f0",
    "tooltip_fg": "#0f172a",
    "on_accent": "#ffffff",
    "slot_icon_bg": "#064e3b",
    "slot_icon_fg": "#34d399",
    "xlsx_slot_bg": "#08221d",
    "xlsx_slot_border": "#059669",
    "xlsx_icon_bg": "#064e3b",
    "xlsx_icon_border": "#047857",
    "xlsx_icon_fg": "#34d399",
    "xlsx_chip_bg": "#064e3b",
    "xlsx_chip_fg": "#6ee7b7",
    "arxml_slot_bg": "#111a36",
    "arxml_slot_border": "#3b82f6",
    "arxml_icon_bg": "#1e1b4b",
    "arxml_icon_border": "#3730a3",
    "arxml_icon_fg": "#818cf8",
    "arxml_chip_bg": "#1e1b4b",
    "arxml_chip_fg": "#a5b4fc",
    "callout_bg": "#162033",
    "callout_border": "#1e293b",
}

THEMES = {"light": LIGHT, "dark": DARK}
THEME_LABELS = {"light": "白天", "dark": "夜间"}


def build_stylesheet(theme: str = "light") -> str:
    """按主题生成整套样式表。"""
    c = THEMES.get(theme, LIGHT)
    return f"""
QWidget {{
    color: {c['text']};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QMainWindow, QWidget#pageContent, QScrollArea#pageScroll, QDialog {{
    background: {c['canvas']};
}}
QScrollArea#pageScroll, QScrollArea#leftScroll {{
    border: none;
    background: transparent;
}}
QScrollArea#pageScroll > QWidget > QWidget,
QScrollArea#leftScroll > QWidget > QWidget {{
    background: transparent;
}}

/* ---------- 顶部全宽导航栏 ---------- */
QFrame#topHeaderBar {{
    background: {c['surface']};
    border: none;
    border-bottom: 1px solid {c['border']};
}}
QLabel#pageTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel#pageSubtitle {{
    font-size: 12px;
    color: {c['text_muted']};
}}
QLabel#versionChip {{
    background: {c['accent_soft']};
    color: {c['accent']};
    border: 1px solid {c['accent_muted']};
    border-radius: 6px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QLabel#readyChip {{
    background: {c['success_chip_bg']};
    color: {c['success_chip_fg']};
    border: 1px solid {c['success_border']};
    border-radius: 10px;
    padding: 1px 9px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#softChip {{
    background: {c['chip_bg']};
    color: {c['chip_fg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QFrame#softChip {{
    background: {c['chip_bg']};
    border: 1px solid {c['border']};
    border-radius: 7px;
}}
QLabel#softChipText {{
    background: transparent;
    color: {c['text_muted']};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#sectionIcon {{
    background: transparent;
}}
QFrame#headerDivider {{
    background: {c['border']};
}}

/* ---------- 主题切换 ---------- */
QFrame#themeSwitch {{
    background: {c['chip_bg']};
    border: 1px solid {c['border']};
    border-radius: 9px;
}}
QPushButton#themeSegment {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 12px;
    color: {c['text_muted']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#themeSegment:hover {{
    color: {c['text']};
}}
QPushButton#themeSegment:checked {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    color: {c['text']};
}}

/* ---------- 左右双栏工作台 ---------- */
QFrame#leftSidebar {{
    background: {c['sidebar_bg']};
    border: none;
    border-right: 1px solid {c['border']};
}}
QWidget#bentoLeft, QWidget#bentoRight, QFrame#bentoColumn, QWidget#bentoColumn {{
    background: transparent;
}}

/* ---------- 头部胶囊按钮 ---------- */
QPushButton#chipButton {{
    background: {c['surface_alt']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 5px 12px;
    color: {c['button_fg']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#chipButton:hover {{
    background: {c['button_hover']};
    border-color: {c['border_strong']};
}}
QPushButton#demoChip {{
    background: {c['surface_alt']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 5px 12px;
    color: {c['text_secondary']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#demoChip:hover {{
    background: {c['accent_soft']};
    border-color: {c['accent_muted']};
    color: {c['accent']};
}}

/* ---------- 文件槽 ---------- */
QFrame#fileSlot {{
    background: {c['surface_alt']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QFrame#fileSlot[variant="xlsx"] {{
    background: {c['xlsx_slot_bg']};
    border: 1px solid {c['xlsx_slot_border']};
}}
QFrame#fileSlot[variant="arxml"] {{
    background: {c['arxml_slot_bg']};
    border: 1px solid {c['arxml_slot_border']};
}}
QFrame#fileSlot:hover {{
    border: 1px solid {c['accent']};
}}
QFrame#fileSlot[dragover="true"] {{
    border: 2px solid {c['accent']};
    background: {c['accent_soft']};
}}
QLabel#fileSlotTitle {{
    font-size: 13px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel#fileSlotSuffix {{
    background: {c['chip_bg']};
    color: {c['chip_fg']};
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 600;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QLabel#fileSlotSuffix[variant="xlsx"] {{
    background: {c['xlsx_chip_bg']};
    color: {c['xlsx_chip_fg']};
}}
QLabel#fileSlotSuffix[variant="arxml"] {{
    background: {c['arxml_chip_bg']};
    color: {c['arxml_chip_fg']};
}}
QLineEdit#fileSlotPath {{
    background: transparent;
    border: none;
    padding: 0;
    color: {c['text_secondary']};
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
}}
QLineEdit#fileSlotPath:focus {{
    background: transparent;
    border: none;
}}
QLineEdit#fileSlotPath::placeholder {{
    color: {c['text_faint']};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 12px;
}}
QPushButton#slotBrowseBtn {{
    background: {c['surface']};
    border: 1px solid {c['border_strong']};
    border-radius: 7px;
    padding: 5px 11px;
    color: {c['button_fg']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#slotBrowseBtn:hover {{
    background: {c['button_hover']};
    border-color: {c['text_faint']};
}}
QPushButton#slotClearBtn {{
    background: transparent;
    border: none;
    padding: 5px 6px;
    color: {c['text_muted']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#slotClearBtn:hover {{
    color: {c['danger_text']};
}}

/* ---------- 输入契约说明框 ---------- */
QFrame#infoCallout {{
    background: {c['callout_bg']};
    border: 1px solid {c['callout_border']};
    border-radius: 10px;
}}
QLabel#fieldHint {{
    color: {c['text_muted']};
    font-size: 12px;
    line-height: 1.45;
}}

/* ---------- 问题列表容器 ---------- */
QFrame#issueListFrame {{
    background: {c['surface']};
    border: none;
    border-top: 1px solid {c['border']};
    border-radius: 0px;
}}
QFrame#issueListFrame QTableView {{
    border: none;
    border-radius: 0px;
}}

/* ---------- 胶囊分段 ---------- */
QFrame#filterPills {{
    background: {c['chip_bg']};
    border: 1px solid {c['border']};
    border-radius: 9px;
}}
QFrame#filterPill {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
}}
QFrame#filterPill:hover {{
    background: {c['ghost_hover']};
}}
QFrame#filterPill[checked="true"] {{
    background: {c['surface']};
    border: 1px solid {c['border']};
}}
QLabel#pillName {{
    color: {c['text_muted']};
    font-size: 12px;
    font-weight: 600;
}}
QFrame#filterPill[checked="true"] QLabel#pillName {{
    color: {c['text']};
    font-weight: 700;
}}
QLabel#filterCount {{
    color: {c['text_muted']};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#filterCount[pillBadge="true"] {{
    background: {c['border']};
    color: {c['text_secondary']};
    border-radius: 5px;
    padding: 0px 6px;
}}

/* ---------- 底部全宽状态栏 ---------- */
QFrame#telemetryBar {{
    background: {c['surface']};
    border: none;
    border-top: 1px solid {c['border']};
}}
QLabel#telemetryText {{
    color: {c['text_muted']};
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 11px;
}}

/* ---------- 流水线步骤 ---------- */
QLabel#stepCrumb {{
    color: {c['text_faint']};
    font-size: 12px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 6px;
}}
QLabel#stepCrumb[active="true"] {{
    background: {c['accent_soft']};
    color: {c['accent']};
    border: 1px solid {c['accent_muted']};
    font-weight: 700;
}}
QLabel#stepCrumbSep {{
    color: {c['border_strong']};
    font-size: 12px;
}}
QLabel#stepCounter {{
    background: {c['chip_bg']};
    color: {c['chip_fg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}

/* ---------- 卡片 ---------- */
QFrame#sectionCard {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 14px;
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel#sectionHint {{
    font-size: 12px;
    color: {c['text_faint']};
}}
QLabel#sectionHintBadge {{
    background: {c['surface_alt']};
    color: {c['text_muted']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#stepBadge {{
    background: {c['accent']};
    color: {c['on_accent']};
    border-radius: 11px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#fieldLabel {{
    color: {c['text_secondary']};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#mutedValue {{
    color: {c['text_muted']};
    font-size: 12px;
}}
QLabel#monoValue {{
    color: {c['text']};
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}}
QLabel#checkValue {{
    color: {c['success_text']};
    font-weight: 600;
    font-size: 12px;
}}
QLabel#checkPlain {{
    color: {c['text']};
    font-size: 12px;
}}
QLabel#outputError {{
    color: {c['danger_text']};
    font-size: 12px;
}}
QLabel#gateHint {{
    color: {c['warning_text']};
    font-size: 12px;
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox {{
    background: {c['input_bg']};
    border: 1px solid {c['border_strong']};
    border-radius: 8px;
    padding: 6px 10px;
    color: {c['text']};
    selection-background-color: {c['accent']};
    selection-color: {c['on_accent']};
}}
QLineEdit#outputPathEdit {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}}
QLineEdit#searchInput {{
    background: {c['input_bg']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 12px;
}}
QLineEdit:hover, QComboBox:hover {{
    border-color: {c['text_faint']};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {c['accent']};
    background: {c['surface']};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background: {c['disabled_bg']};
    color: {c['disabled_fg']};
}}
QLineEdit::placeholder {{
    color: {c['text_faint']};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {c['button_bg']};
    border: 1px solid {c['border_strong']};
    border-radius: 8px;
    padding: 6px 13px;
    color: {c['button_fg']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {c['button_hover']};
    border-color: {c['text_faint']};
}}
QPushButton:pressed {{
    background: {c['button_pressed']};
}}
QPushButton:disabled {{
    background: {c['disabled_bg']};
    border-color: {c['border']};
    color: {c['disabled_fg']};
}}
QPushButton#previewButton {{
    background: {c['surface']};
    border: 1px solid {c['accent_muted']};
    color: {c['accent']};
    font-weight: 700;
}}
QPushButton#previewButton:hover {{
    background: {c['accent_soft']};
    border-color: {c['accent']};
}}
QPushButton#previewButton:disabled {{
    background: {c['disabled_bg']};
    border-color: {c['border']};
    color: {c['disabled_fg']};
}}
QPushButton#primaryButton {{
    background: {c['accent']};
    border: 1px solid {c['accent']};
    color: {c['on_accent']};
    font-weight: 700;
}}
QPushButton#primaryButton:hover {{
    background: {c['accent_hover']};
    border-color: {c['accent_hover']};
}}
QPushButton#primaryButton:pressed {{
    background: {c['accent_pressed']};
    border-color: {c['accent_pressed']};
}}
QPushButton#primaryButton:disabled {{
    background: {c['disabled_bg']};
    border-color: {c['border']};
    color: {c['disabled_fg']};
}}
QPushButton#ackBatchButton {{
    background: {c['ack_btn_bg']};
    border: 1px solid {c['ack_btn_bg']};
    color: {c['ack_btn_fg']};
    font-weight: 700;
}}
QPushButton#ackBatchButton:hover {{
    background: {c['ack_btn_hover']};
    border-color: {c['ack_btn_hover']};
}}
QPushButton#ackBatchButton:disabled {{
    background: {c['disabled_bg']};
    border-color: {c['border']};
    color: {c['disabled_fg']};
}}
QPushButton#ghostButton {{
    background: transparent;
    border: 1px solid transparent;
    color: {c['text_muted']};
    padding: 6px 10px;
}}
QPushButton#ghostButton:hover {{
    background: {c['ghost_hover']};
    color: {c['text']};
}}
QPushButton#ghostButton:disabled {{
    background: transparent;
    color: {c['disabled_fg']};
}}
QPushButton#dangerButton {{
    background: {c['neutral_soft']};
    border: 1px solid {c['border']};
    color: {c['text_muted']};
    padding: 6px 11px;
}}
QPushButton#dangerButton:hover {{
    background: {c['danger_soft']};
    border-color: {c['danger_border']};
    color: {c['danger_text']};
}}
QPushButton#dangerButton:disabled {{
    background: {c['disabled_bg']};
    border-color: {c['border']};
    color: {c['disabled_fg']};
}}
QPushButton#linkButton {{
    background: transparent;
    border: none;
    color: {c['accent']};
    font-size: 12px;
    font-weight: 600;
    padding: 2px 4px;
    text-align: left;
}}
QPushButton#linkButton:hover {{
    color: {c['accent_hover']};
    text-decoration: underline;
}}

/* ---------- 顶部状态条 ---------- */
QFrame#statusBanner {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QFrame#statusRail {{
    background: {c['accent']};
    border-radius: 2px;
}}
QFrame#statusRail[state="neutral"] {{ background: {c['text_faint']}; }}
QFrame#statusRail[state="info"] {{ background: {c['accent']}; }}
QFrame#statusRail[state="success"] {{ background: {c['success']}; }}
QFrame#statusRail[state="warning"] {{ background: {c['warning']}; }}
QFrame#statusRail[state="error"] {{ background: {c['danger']}; }}
QLabel#statusTitle {{
    font-size: 13px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel#statusDetail {{
    font-size: 12px;
    color: {c['text_muted']};
}}

QProgressBar {{
    background: {c['border']};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: 3px;
}}

/* ---------- 摘要卡片 ---------- */
QFrame#featureCard {{
    background: {c['surface_alt']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
QFrame#featureCard[tone="add"] {{ border-left: 3px solid {c['success']}; }}
QFrame#featureCard[tone="delete"] {{ border-left: 3px solid {c['danger']}; }}
QFrame#featureCard[tone="info"] {{ border-left: 3px solid {c['accent']}; }}
QLabel#featureTitle {{
    font-size: 13px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel#featureStatus {{
    font-size: 11px;
    color: {c['text_muted']};
}}
QLabel#featureMetric {{
    color: {c['text_muted']};
    font-size: 12px;
}}

/* ---------- 问题表 ---------- */
QTableView {{
    background: {c['surface']};
    alternate-background-color: {c['table_alt']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {c['table_selected']};
    selection-color: {c['text']};
    outline: none;
}}
QTableView::item {{
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {c['table_line']};
}}
QTableView::item:selected {{
    background: {c['table_selected']};
    color: {c['text']};
}}
QHeaderView::section {{
    background: {c['table_header']};
    color: {c['text_muted']};
    padding: 9px 10px;
    border: none;
    border-bottom: 1px solid {c['border']};
    font-size: 12px;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background: {c['table_header']};
    border: none;
}}
QLabel#emptyHint {{
    color: {c['text_faint']};
    font-size: 13px;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 1px;
}}
QScrollBar::handle:vertical {{
    background: {c['scroll']};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['scroll_hover']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 1px;
}}
QScrollBar::handle:horizontal {{
    background: {c['scroll']};
    border-radius: 3px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c['scroll_hover']};
}}

/* ---------- 对话框与提示 ---------- */
QMessageBox, QDialog {{
    background: {c['surface']};
}}
QMessageBox QLabel {{
    color: {c['text']};
}}
QToolTip {{
    background: {c['tooltip_bg']};
    color: {c['tooltip_fg']};
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
}}
"""


def detect_system_theme() -> str:
    """读取 Windows 的应用主题；不可用时回退到白天主题。"""
    if sys.platform != "win32":
        return "light"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return "light"
    return "light" if value else "dark"


def apply_theme(app, theme: str) -> None:
    """应用主题样式与调色板，使原生绘制的控件也跟随主题。"""
    c = THEMES.get(theme, LIGHT)
    app.setStyleSheet(build_stylesheet(theme))
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(c["surface"]))
    palette.setColor(QPalette.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.Base, QColor(c["input_bg"]))
    palette.setColor(QPalette.AlternateBase, QColor(c["table_alt"]))
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.Button, QColor(c["button_bg"]))
    palette.setColor(QPalette.ButtonText, QColor(c["button_fg"]))
    palette.setColor(QPalette.BrightText, QColor(c["danger"]))
    palette.setColor(QPalette.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(c["on_accent"]))
    palette.setColor(QPalette.ToolTipBase, QColor(c["tooltip_bg"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["tooltip_fg"]))
    palette.setColor(QPalette.PlaceholderText, QColor(c["text_faint"]))
    palette.setColor(QPalette.Light, QColor(c["surface_alt"]))
    palette.setColor(QPalette.Mid, QColor(c["border"]))
    palette.setColor(QPalette.Dark, QColor(c["canvas"]))
    app.setPalette(palette)


# 兼容旧引用：默认主题的样式表。
APP_STYLE = build_stylesheet("light")
