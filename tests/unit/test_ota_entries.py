"""验证各入口遵守完整预览、明确确认和输入变更后的失效边界。"""

import asyncio
from pathlib import Path

from davinci_gw.cli import main
from davinci_gw.mcp.ota_service import OtaMcpService
from davinci_gw.mcp.runtime import BoundedFacadeRuntime


class FakeOta:
    def __init__(self):
        self.generated = []

    def check(self, baseline):
        return {"can_generate": True, "changes": [], "issues": []}

    def preview(self, baseline):
        return {"can_generate": True, "preparation_id": "prepared", "issues": [],
                "changes": [{"path": f"/Com/Group{i}", "before": None, "after": i} for i in range(205)]}

    def generate(self, preparation_id, output, confirmed=False, *, cancellation=None):
        assert confirmed
        self.generated.append(output)
        return {"output_path": output, "generated": True}


def test_cli_preview_precedes_confirmation(monkeypatch, capsys):
    fake = FakeOta()
    monkeypatch.setattr("davinci_gw.application.ota.OtaService", lambda: fake)

    def confirm(_prompt):
        assert '"/Com/Group204"' in capsys.readouterr().out
        assert not fake.generated
        return "YES"

    monkeypatch.setattr("builtins.input", confirm)
    assert main(["ota-generate", "--baseline", "baseline.arxml", "--output", "new.arxml"]) == 0
    assert fake.generated == ["new.arxml"]


def test_cli_refusal_does_not_generate(monkeypatch):
    fake = FakeOta()
    monkeypatch.setattr("davinci_gw.application.ota.OtaService", lambda: fake)
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    assert main(["ota-generate", "--baseline", "baseline.arxml", "--output", "new.arxml"]) == 2
    assert not fake.generated


def test_mcp_requires_every_preview_page_confirmation_and_safe_output(tmp_path: Path):
    baseline = tmp_path / "base.arxml"
    baseline.write_text("input", encoding="utf-8")

    async def scenario():
        runtime = BoundedFacadeRuntime()
        fake = FakeOta()
        service = OtaMcpService(runtime, fake)
        try:
            unsafe = await service.invoke("preview", baseline_path="https://example.org/base.arxml")
            assert not unsafe["success"]
            page = await service.invoke("preview", baseline_path=str(baseline))
            assert len(page["changes"]) == 100
            assert page["next_offset"] == 100
            assert not (await service.invoke("generate", preparation_id="prepared", output_path=str(tmp_path / "new.arxml"), confirmed=True))["success"]
            wrong = await service.invoke("preview", baseline_path=str(baseline), preparation_id="prepared", offset=200)
            assert not wrong["success"]
            second = await service.invoke("preview", baseline_path=str(baseline), preparation_id="prepared", offset=100)
            assert second["changes"][0]["path"] == "/Com/Group100"
            last = await service.invoke("preview", baseline_path=str(baseline), preparation_id="prepared", offset=200)
            assert len(last["changes"]) == 5 and last["preview_complete"]
            assert not (await service.invoke("generate", preparation_id="prepared", output_path=str(baseline), confirmed=True))["success"]
            assert not (await service.invoke("generate", preparation_id="prepared", output_path=str(tmp_path / "new.arxml")))["success"]
            generated = await service.invoke("generate", preparation_id="prepared", output_path=str(tmp_path / "new.arxml"), confirmed=True)
            assert generated["success"] and len(fake.generated) == 1
            assert not (await service.invoke("generate", preparation_id="prepared", output_path=str(tmp_path / "other.arxml"), confirmed=True))["success"]
            assert baseline.read_text(encoding="utf-8") == "input"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_gui_async_preview_and_input_change_invalidate_session(qtbot):
    from UI.ota_dialog import OtaDialog

    dialog = OtaDialog("baseline.arxml", service=FakeOta())
    qtbot.addWidget(dialog)
    dialog._inspect(True)
    qtbot.waitUntil(lambda: dialog._task is None)
    assert dialog.generate_button.isEnabled()
    assert "/Com/Group204" in dialog.result_view.toPlainText()
    dialog.baseline_edit.setText("changed.arxml")
    assert not dialog.generate_button.isEnabled()
    assert not dialog._preparation_id
