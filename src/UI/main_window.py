"""DaVinci 网关路由工具的单窗口 Qt Widgets 界面。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from davinci_gw.contracts import FeatureSummaryDto

from .issue_model import IssueFilterProxyModel, IssueTableModel
from .detail_dialog import FeatureDetailDialog
from .logging_config import log_path
from .state import GuiState
from .view_model import GatewayViewModel


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


class MainWindow(QMainWindow):
    """把四步工作流、结果和问题集中在一个可滚动窗口。"""

    def __init__(self, view_model: GatewayViewModel) -> None:
        super().__init__()
        self.vm = view_model
        self.settings = QSettings()
        self._allow_close = False
        self._closing = False
        self._detail_dialog: FeatureDetailDialog | None = None
        self.setWindowTitle("DaVinci 网关路由工具")
        self.setMinimumSize(980, 680)
        self.resize(1180, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect()
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._rebuild_summary()
        self._sync()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)

        title = QLabel("DaVinci 网关路由配置生成")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        subtitle = QLabel("选择标准配置表和基准 ARXML，先预览，再生成新文件。")
        root.addWidget(title)
        root.addWidget(subtitle)

        inputs = QGroupBox("1–2  选择输入文件")
        grid = QGridLayout(inputs)
        self.config_edit = FileDropEdit(".xlsx")
        self.config_edit.setPlaceholderText("选择或拖入标准配置表（.xlsx）")
        self.config_edit.setAccessibleName("标准配置表路径")
        self.config_button = QPushButton("浏览…")
        self.config_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.config_clear = QPushButton("清除")
        self.baseline_edit = FileDropEdit(".arxml")
        self.baseline_edit.setPlaceholderText("选择或拖入基准 ARXML（.arxml）")
        self.baseline_edit.setAccessibleName("基准 ARXML 路径")
        self.baseline_button = QPushButton("浏览…")
        self.baseline_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.baseline_clear = QPushButton("清除")
        grid.addWidget(QLabel("标准配置表"), 0, 0)
        grid.addWidget(self.config_edit, 0, 1)
        grid.addWidget(self.config_button, 0, 2)
        grid.addWidget(self.config_clear, 0, 3)
        grid.addWidget(QLabel("基准 ARXML"), 1, 0)
        grid.addWidget(self.baseline_edit, 1, 1)
        grid.addWidget(self.baseline_button, 1, 2)
        grid.addWidget(self.baseline_clear, 1, 3)
        grid.setColumnStretch(1, 1)
        root.addWidget(inputs)

        actions = QGroupBox("3–4  预览并生成")
        action_layout = QGridLayout(actions)
        self.output_edit = QLineEdit()
        self.output_edit.setAccessibleName("输出 ARXML 路径")
        self.output_button = QPushButton("选择输出…")
        self.preview_button = QPushButton("预览变更")
        self.preview_button.setObjectName("primaryButton")
        self.generate_button = QPushButton("生成 ARXML")
        self.generate_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton("取消")
        self.output_error = QLabel()
        self.output_error.setObjectName("outputError")
        action_layout.addWidget(QLabel("输出文件"), 0, 0)
        action_layout.addWidget(self.output_edit, 0, 1, 1, 3)
        action_layout.addWidget(self.output_button, 0, 4)
        action_layout.addWidget(self.output_error, 1, 1, 1, 4)
        action_buttons = QHBoxLayout()
        action_buttons.addStretch()
        action_buttons.addWidget(self.preview_button)
        action_buttons.addWidget(self.generate_button)
        action_buttons.addWidget(self.cancel_button)
        action_layout.addLayout(action_buttons, 2, 1, 1, 4)
        action_layout.setColumnStretch(1, 1)
        root.addWidget(actions)

        self.status_banner = QLabel()
        self.status_banner.setObjectName("statusBanner")
        self.status_banner.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.status_banner)
        root.addWidget(self.progress)

        self.result_group = QGroupBox("生成结果")
        result_layout = QHBoxLayout(self.result_group)
        self.result_path = QLabel()
        self.result_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder_button = QPushButton("打开所在文件夹")
        self.copy_path_button = QPushButton("复制路径")
        result_layout.addWidget(self.result_path, 1)
        result_layout.addWidget(self.open_folder_button)
        result_layout.addWidget(self.copy_path_button)
        root.addWidget(self.result_group)

        self.check_group = QGroupBox("输入检查")
        self.check_layout = QGridLayout(self.check_group)
        root.addWidget(self.check_group)

        self.feature_group = QGroupBox("变更摘要")
        self.feature_layout = QGridLayout(self.feature_group)
        root.addWidget(self.feature_group)

        issues = QGroupBox("问题与保留决定")
        issue_layout = QVBoxLayout(issues)
        filters = QHBoxLayout()
        self.severity_filter = QComboBox()
        for label, value in (("全部", "ALL"), ("错误", "ERROR"), ("警告", "WARNING"),
                             ("提示", "INFO"), ("保留决定", "DECISION")):
            self.severity_filter.addItem(label, value)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索代码、说明、位置或建议")
        self.copy_issues_button = QPushButton("复制所选")
        filters.addWidget(QLabel("级别"))
        filters.addWidget(self.severity_filter)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.copy_issues_button)
        self.issue_model = IssueTableModel(self)
        self.issue_proxy = IssueFilterProxyModel(self)
        self.issue_proxy.setSourceModel(self.issue_model)
        self.issue_table = QTableView()
        self.issue_table.setModel(self.issue_proxy)
        self.issue_table.setSortingEnabled(True)
        self.issue_table.setSelectionBehavior(QTableView.SelectRows)
        self.issue_table.setMinimumHeight(190)
        self.issue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.issue_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        issue_layout.addLayout(filters)
        issue_layout.addWidget(self.issue_table)
        root.addWidget(issues)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("高级信息")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_panel = QFrame()
        advanced_layout = QGridLayout(self.advanced_panel)
        self.operation_label = QLabel("—")
        self.session_label = QLabel("—")
        self.version_label = QLabel("1.1.2")
        self.open_log_button = QPushButton("打开日志目录")
        advanced_layout.addWidget(QLabel("操作 ID"), 0, 0)
        advanced_layout.addWidget(self.operation_label, 0, 1)
        advanced_layout.addWidget(QLabel("会话 ID"), 1, 0)
        advanced_layout.addWidget(self.session_label, 1, 1)
        advanced_layout.addWidget(QLabel("版本"), 2, 0)
        advanced_layout.addWidget(self.version_label, 2, 1)
        advanced_layout.addWidget(self.open_log_button, 3, 1)
        self.advanced_panel.setVisible(False)
        root.addWidget(self.advanced_toggle)
        root.addWidget(self.advanced_panel)
        root.addStretch()
        scroll.setWidget(content)
        self.setCentralWidget(scroll)

        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.vm.start_preview)
        QShortcut(QKeySequence("Ctrl+G"), self, activated=self.vm.start_generate)
        QShortcut(QKeySequence("Esc"), self, activated=self.vm.cancel)

    def _connect(self) -> None:
        self.vm.changed.connect(self._sync)
        self.vm.issues_changed.connect(self.issue_model.set_issues)
        self.vm.preview_changed.connect(self._rebuild_summary)
        self.vm.shutdown_finished.connect(self._finish_close)
        self.config_button.clicked.connect(lambda: self._browse_input("config"))
        self.baseline_button.clicked.connect(lambda: self._browse_input("baseline"))
        self.config_clear.clicked.connect(lambda: self.vm.set_config_path(""))
        self.baseline_clear.clicked.connect(lambda: self.vm.set_baseline_path(""))
        self.config_edit.path_dropped.connect(self.vm.set_config_path)
        self.baseline_edit.path_dropped.connect(self.vm.set_baseline_path)
        self.config_edit.editingFinished.connect(lambda: self.vm.set_config_path(self.config_edit.text()))
        self.baseline_edit.editingFinished.connect(lambda: self.vm.set_baseline_path(self.baseline_edit.text()))
        self.output_edit.textEdited.connect(lambda text: self.vm.set_output_path(text, manual=True))
        self.output_button.clicked.connect(self._browse_output)
        self.preview_button.clicked.connect(self.vm.start_preview)
        self.generate_button.clicked.connect(self.vm.start_generate)
        self.cancel_button.clicked.connect(self.vm.cancel)
        self.severity_filter.currentIndexChanged.connect(
            lambda: self.issue_proxy.set_severity(self.severity_filter.currentData())
        )
        self.search_edit.textChanged.connect(self.issue_proxy.set_search)
        self.copy_issues_button.clicked.connect(self._copy_issues)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.open_folder_button.clicked.connect(self._open_artifact_folder)
        self.copy_path_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.vm.artifact_path))
        self.open_log_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path().parent))))

    def _browse_input(self, role: str) -> None:
        recent = str(self.settings.value(f"recent/{role}", ""))
        filter_text = "Excel 配置表 (*.xlsx)" if role == "config" else "AUTOSAR XML (*.arxml)"
        path, _ = QFileDialog.getOpenFileName(self, "选择输入文件", recent, filter_text)
        if not path:
            return
        self.settings.setValue(f"recent/{role}", str(Path(path).parent))
        (self.vm.set_config_path if role == "config" else self.vm.set_baseline_path)(path)

    def _browse_output(self) -> None:
        start = self.vm.output_path or str(self.settings.value("recent/output", ""))
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", start, "AUTOSAR XML (*.arxml)")
        if path:
            if not path.lower().endswith(".arxml"):
                path += ".arxml"
            self.settings.setValue("recent/output", str(Path(path).parent))
            self.vm.set_output_path(path, manual=True)

    def _sync(self) -> None:
        controls = self.vm.controls
        if self.config_edit.text() != self.vm.config_path:
            self.config_edit.setText(self.vm.config_path)
        if self.baseline_edit.text() != self.vm.baseline_path:
            self.baseline_edit.setText(self.vm.baseline_path)
        if self.output_edit.text() != self.vm.output_path:
            self.output_edit.setText(self.vm.output_path)
        for widget in (self.config_edit, self.config_button, self.config_clear,
                       self.baseline_edit, self.baseline_button, self.baseline_clear):
            widget.setEnabled(controls.inputs_enabled)
        self.output_edit.setEnabled(controls.inputs_enabled)
        self.output_button.setEnabled(controls.inputs_enabled)
        self.preview_button.setEnabled(controls.preview_enabled)
        self.generate_button.setEnabled(controls.generate_enabled)
        self.cancel_button.setEnabled(controls.cancel_enabled)
        state_text = {
            GuiState.EMPTY: "等待输入", GuiState.READY: "输入就绪",
            GuiState.PREPARING: "正在预览", GuiState.PREVIEW_VALID: "预览通过",
            GuiState.PREVIEW_INVALID: "预览未通过", GuiState.GENERATING: "正在生成",
            GuiState.SUCCESS: "生成成功", GuiState.FAILED: "操作失败",
            GuiState.CANCELLED: "操作已取消",
        }[self.vm.state]
        self.status_banner.setText(f"{state_text} · {self.vm.stage_name}")
        self.progress.setValue(self.vm.progress_percent)
        self.progress.setVisible(self.vm.state in {GuiState.PREPARING, GuiState.GENERATING})
        self.output_error.setText(self.vm.output_error)
        self.result_group.setVisible(bool(self.vm.artifact_path))
        self.result_path.setText(self.vm.artifact_path)
        self.operation_label.setText(self.vm.operation_id or "—")
        self.session_label.setText(self.vm.session_id or "—")

    def _rebuild_summary(self) -> None:
        self._clear_layout(self.check_layout)
        row = 0
        for input_file in self.vm.input_files:
            size = input_file.fingerprint.size if input_file.fingerprint else 0
            self.check_layout.addWidget(QLabel("配置表" if input_file.role == "CONFIG" else "基准 ARXML"), row, 0)
            self.check_layout.addWidget(QLabel(f"{Path(input_file.path).name} · {size / 1024 / 1024:.2f} MB"), row, 1)
            row += 1
        if self.vm.target_version:
            self.check_layout.addWidget(QLabel("目标版本"), row, 0)
            self.check_layout.addWidget(QLabel(self.vm.target_version), row, 1)
            row += 1
        for check in self.vm.checks:
            self.check_layout.addWidget(QLabel(check.display_name), row, 0)
            self.check_layout.addWidget(QLabel(f"✓ {check.detail or check.status}"), row, 1)
            row += 1
        self.check_group.setVisible(row > 0)

        self._clear_layout(self.feature_layout)
        for index, feature in enumerate(self.vm.features):
            card = QFrame()
            card.setObjectName("featureCard")
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            layout = QVBoxLayout(card)
            heading = QLabel(f"{feature.display_name} · {feature.status}")
            heading.setStyleSheet("font-weight: 700;")
            layout.addWidget(heading)
            for metric in feature.metrics:
                layout.addWidget(QLabel(f"{metric.label}：{metric.value}"))
            if feature.details:
                detail_button = QPushButton("查看详情")
                detail_button.setAccessibleName(f"查看{feature.display_name}详情")
                detail_button.clicked.connect(
                    lambda _checked=False, item=feature: self._show_feature_details(item),
                )
                layout.addWidget(detail_button)
            self.feature_layout.addWidget(card, index // 2, index % 2)
        self.feature_group.setVisible(bool(self.vm.features))

    def _show_feature_details(self, feature: FeatureSummaryDto) -> None:
        if self._detail_dialog is not None:
            self._detail_dialog.close()
        self._detail_dialog = FeatureDetailDialog(feature, self)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _copy_issues(self) -> None:
        rows = {self.issue_proxy.mapToSource(index).row() for index in self.issue_table.selectionModel().selectedRows()}
        text = self.issue_model.copy_text(rows)
        if text:
            QApplication.clipboard().setText(text)

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.advanced_panel.setVisible(checked)

    def _open_artifact_folder(self) -> None:
        if self.vm.artifact_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.vm.artifact_path).parent)))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        suffixes = {Path(url.toLocalFile()).suffix.lower() for url in event.mimeData().urls() if url.isLocalFile()}
        if suffixes & {".xlsx", ".arxml"}:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            suffix = Path(path).suffix.lower()
            if suffix == ".xlsx":
                self.vm.set_config_path(path)
            elif suffix == ".arxml":
                self.vm.set_baseline_path(path)
        event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._allow_close:
            self.settings.setValue("window/geometry", self.saveGeometry())
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        if self.vm.runner.is_busy:
            answer = QMessageBox.question(
                self,
                "正在处理",
                "当前操作尚未完成。是否安全取消并退出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._closing = True
        self.setEnabled(False)
        self.vm.shutdown()
        event.ignore()

    def _finish_close(self) -> None:
        self._allow_close = True
        self.close()
