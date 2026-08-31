"""只覆盖 GUI 最关键的状态、动态展示和线程闭环。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QLabel, QPushButton

from UI.issue_model import IssueTableModel
from UI.main_window import MainWindow
from UI.output_paths import suggest_output_path, validate_output_path
from UI.state import GuiState, controls_for
from UI.task_runner import QtTaskRunner
from UI.view_model import GatewayViewModel
from davinci_gw.application.public_mapping import decisions_to_dto, issues_to_dto
from davinci_gw.contracts import ChangeDetailDto, FeatureSummaryDto, MetricDto, OperationStatus
from davinci_gw.domain.models import RetentionDecision, SourceLocation, ValidationIssue
from davinci_gw.input.workbook_reader import read_workbook


class _FakeRunner(QObject):
    progress = Signal(object)
    preview_finished = Signal(object)
    generation_finished = Signal(object)
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.is_busy = False
        self.discarded: list[str] = []

    def discard(self, session_id: str) -> None:
        self.discarded.append(session_id)

    def shutdown(self) -> None:
        self.stopped.emit()

    def cancel_current(self) -> None:
        pass


def test_state_output_and_issue_message_identity_rules(tmp_path: Path, workbook_factory) -> None:
    baseline = tmp_path / "项目基线.arxml"
    baseline.write_text("x", encoding="utf-8")
    first = Path(suggest_output_path(str(baseline), "4.84"))
    first.write_text("x", encoding="utf-8")
    second = Path(suggest_output_path(str(baseline), "4.84"))

    assert second.name == "项目基线_v4.84_2.arxml"
    assert validate_output_path(str(baseline), str(baseline)) is not None
    assert controls_for(GuiState.PREVIEW_VALID, inputs_valid=True, cancellable=False).generate_enabled
    assert not controls_for(GuiState.GENERATING, inputs_valid=True, cancellable=True).inputs_enabled

    workbook = read_workbook(workbook_factory()).data
    decision = decisions_to_dto((
        RetentionDecision("/Cfg/Shared", "仍被其他路由使用", "DIRECT_SHARED", (SourceLocation("直接报文路由", 2),)),
    ), workbook)[0]
    assert decision.severity == "DECISION"
    assert [(item.name, item.can_id) for item in decision.messages] == [
        ("SRC_MSG", "0x100"),
        ("DST_MSG", "0x200"),
    ]
    model = IssueTableModel()
    model.set_issues((decision,))
    assert model.headerData(2, Qt.Horizontal) == "报文 ID"
    assert model.headerData(3, Qt.Horizontal) == "报文名称"
    assert model.index(0, 2).data() == "源：0x100 → 目标：0x200"
    assert model.index(0, 3).data() == "源：SRC_MSG → 目标：DST_MSG"

    issue = issues_to_dto((
        ValidationIssue("ROUTE_TEST", "路由问题", location=SourceLocation("直接报文路由", 2)),
    ), workbook)[0]
    assert [(item.role, item.name, item.can_id) for item in issue.messages] == [
        ("SOURCE", "SRC_MSG", "0x100"),
        ("TARGET", "DST_MSG", "0x200"),
    ]


def test_feature_cards_are_created_from_dto_without_fixed_ids(qtbot) -> None:
    runner = _FakeRunner()
    view_model = GatewayViewModel(runner)  # type: ignore[arg-type]
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    view_model.features = (
        FeatureSummaryDto(
            "diagnostic_virtual", "虚拟诊断路由", "READY", (MetricDto("count", "数量", 3),),
            details=(ChangeDetailDto("ADD", "诊断", "源端", "目标端", "诊断路由 第 2 行"),),
        ),
    )
    view_model.preview_changed.emit()

    labels = {label.text() for label in window.feature_group.findChildren(QLabel)}
    assert "虚拟诊断路由 · READY" in labels
    assert "数量：3" in labels
    detail_button = next(
        button for button in window.feature_group.findChildren(QPushButton)
        if button.text() == "查看详情"
    )
    qtbot.mouseClick(detail_button, Qt.LeftButton)
    assert window._detail_dialog is not None
    assert window._detail_dialog.model.rowCount() == 1
    assert window._detail_dialog.model.item(0, 0).text() == "新增"


def test_single_worker_thread_previews_and_commits_prepared_session(
    qtbot, workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    runner = QtTaskRunner()
    config = workbook_factory()
    baseline = arxml_factory()
    with qtbot.waitSignal(runner.preview_finished, timeout=15_000) as preview_wait:
        runner.start_preview("preview", str(config), str(baseline))
    prepared = preview_wait.args[0]
    assert prepared.status is OperationStatus.SUCCESS
    assert prepared.session_id
    features = {feature.feature_id: feature for feature in prepared.preview.features}
    assert [(detail.action, detail.item_type) for detail in features["direct_message"].details] == [
        ("ADD", "报文"),
    ]
    assert [(detail.action, detail.item_type) for detail in features["signal_route"].details] == [
        ("ADD", "信号"),
    ]
    assert {check.check_id for check in prepared.preview.checks} >= {
        "arxml_schema", "module_canif", "module_com", "module_ecuc", "module_pdur",
    }

    output = tmp_path / "GUI 输出.arxml"
    with qtbot.waitSignal(runner.generation_finished, timeout=15_000) as generation_wait:
        runner.start_commit("commit", prepared.session_id, str(output))
    generated = generation_wait.args[0]
    assert generated.status is OperationStatus.SUCCESS
    assert output.is_file()

    with qtbot.waitSignal(runner.stopped, timeout=5_000):
        runner.shutdown()
