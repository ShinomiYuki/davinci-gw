"""应用层编排与中文 CLI 测试。"""

from __future__ import annotations

from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.validate import validate_inputs
from davinci_gw.cli import main


def test_validate_and_preview(workbook_factory: object, arxml_factory: object) -> None:
    config = workbook_factory()
    baseline = arxml_factory()
    report = validate_inputs(config, baseline)
    preview = preview_inputs(config, baseline)
    assert report.is_valid
    assert preview.is_valid
    assert preview.direct_add_count == 1
    assert preview.signal_add_count == 1
    assert preview.reference_count == 2


def test_cli_preview_chinese_output(
    workbook_factory: object, arxml_factory: object, capsys: object,
) -> None:
    code = main(["preview", "--config", str(workbook_factory()),
                 "--baseline", str(arxml_factory())])
    output = capsys.readouterr().out
    assert code == 0
    assert "目标版本：4.84" in output
    assert "当前开发轮次尚未执行路由写入" in output


def test_cli_contract_error_exit_code(
    workbook_factory: object, arxml_factory: object, capsys: object,
) -> None:
    code = main(["validate", "--config", str(workbook_factory(filename="bad.xlsx")),
                 "--baseline", str(arxml_factory())])
    assert code == 2
    assert "无法从配置表文件名识别目标版本" in capsys.readouterr().out


def test_cli_system_error_exit_code(
    workbook_factory: object, tmp_path: object, capsys: object,
) -> None:
    broken = tmp_path / "broken.arxml"
    broken.write_text("<broken", encoding="utf-8")
    code = main(["validate", "--config", str(workbook_factory()),
                 "--baseline", str(broken)])
    assert code == 3
    assert "无法解析" in capsys.readouterr().out
