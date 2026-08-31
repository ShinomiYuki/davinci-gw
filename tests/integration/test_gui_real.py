"""真实大型 ARXML 的 GUI 响应性、取消和 Prepared Session 验收。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import monotonic

import pytest
from PySide6.QtCore import QTimer, Qt

from UI.main_window import MainWindow
from UI.state import GuiState
from UI.task_runner import QtTaskRunner
from UI.view_model import GatewayViewModel
from davinci_gw.arxml.document import ArxmlDocument

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "input" / "网关路由配置表_v4.84.xlsx"
BASELINE_PATH = PROJECT_ROOT / "input" / "825E0GA.arxml"


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.slow
def test_real_gui_cancel_preview_commit_remains_responsive(
    qtbot, tmp_path: Path, monkeypatch,
) -> None:
    if not CONFIG_PATH.is_file() or not BASELINE_PATH.is_file():
        pytest.skip("本地真实 GUI 输入不存在")

    baseline_before = (BASELINE_PATH.stat().st_size, BASELINE_PATH.stat().st_mtime_ns, _digest(BASELINE_PATH))
    baseline_resolved = BASELINE_PATH.resolve()
    load_count = 0
    original_load = ArxmlDocument.load.__func__

    def load_spy(cls, path):
        nonlocal load_count
        if Path(path).resolve() == baseline_resolved:
            load_count += 1
        return original_load(cls, path)

    monkeypatch.setattr(ArxmlDocument, "load", classmethod(load_spy))
    runner = QtTaskRunner()
    view_model = GatewayViewModel(runner)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    output = tmp_path / "真实 GUI 输出 空格.arxml"
    view_model.set_config_path(str(CONFIG_PATH))
    view_model.set_baseline_path(str(BASELINE_PATH))
    view_model.set_output_path(str(output), manual=True)

    heartbeats: list[float] = []
    geometry_changes = 0
    heartbeat = QTimer(window)
    heartbeat.setInterval(50)

    def tick() -> None:
        nonlocal geometry_changes
        heartbeats.append(monotonic())
        if runner.is_busy:
            geometry_changes += 1
            window.resize(1080 + geometry_changes % 3, 760 + geometry_changes % 2)
            window.move(20 + geometry_changes % 4, 20 + geometry_changes % 4)

    heartbeat.timeout.connect(tick)
    heartbeat.start()

    qtbot.mouseClick(window.preview_button, Qt.LeftButton)
    QTimer.singleShot(200, lambda: qtbot.mouseClick(window.cancel_button, Qt.LeftButton))
    qtbot.waitUntil(lambda: view_model.state is GuiState.CANCELLED, timeout=120_000)
    assert not output.exists()

    loads_before_full_preview = load_count
    qtbot.mouseClick(window.preview_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: view_model.state is GuiState.PREVIEW_VALID, timeout=180_000)
    assert view_model.session_id
    assert view_model.features
    assert len(view_model.checks) >= 5

    qtbot.mouseClick(window.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: view_model.state is GuiState.SUCCESS, timeout=180_000)
    heartbeat.stop()
    assert output.is_file() and output.stat().st_size > 80_000_000
    assert load_count - loads_before_full_preview == 1
    assert geometry_changes > 2
    assert len(heartbeats) > 5
    assert max(right - left for left, right in zip(heartbeats, heartbeats[1:], strict=False)) < 1.0
    assert baseline_before == (
        BASELINE_PATH.stat().st_size, BASELINE_PATH.stat().st_mtime_ns, _digest(BASELINE_PATH),
    )
    assert not tuple(tmp_path.glob(".*.tmp.arxml"))

    with qtbot.waitSignal(runner.stopped, timeout=10_000):
        runner.shutdown()
