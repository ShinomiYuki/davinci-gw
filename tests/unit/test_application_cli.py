"""应用层编排与中文 CLI 测试。"""

from __future__ import annotations

import pytest

from davinci_gw.application.generate import generate_inputs
from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.validate import validate_inputs
from davinci_gw.cli import main
from davinci_gw.domain.errors import OutputValidationError
from tests.conftest import direct_row


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


def test_generate_rejects_delete_and_writes_nothing(
    workbook_factory: object, arxml_factory: object, tmp_path: object, capsys: object,
) -> None:
    output = tmp_path / "blocked.arxml"
    config = workbook_factory(
        signal_rows=(), direct_rows=(direct_row(**{"操作类型": "DELETE"}),),
    )
    code = main(["generate", "--config", str(config), "--baseline", str(arxml_factory()),
                 "--output", str(output)])
    assert code == 2
    assert not output.exists()
    assert "当前版本仅实现ADD" in capsys.readouterr().out


def test_generate_cli_success_summary(
    workbook_factory: object, arxml_factory: object, tmp_path: object, capsys: object,
) -> None:
    output = tmp_path / "generated.arxml"
    code = main(["generate", "--config", str(workbook_factory(signal_rows=())),
                 "--baseline", str(arxml_factory()), "--output", str(output)])
    text = capsys.readouterr().out
    assert code == 0 and output.exists()
    assert "直接报文路由" in text and "输出验证：通过" in text


def test_generate_refuses_existing_output(
    workbook_factory: object, arxml_factory: object, tmp_path: object, capsys: object,
) -> None:
    output = tmp_path / "exists.arxml"
    output.write_text("keep", encoding="utf-8")
    code = main(["generate", "--config", str(workbook_factory(signal_rows=())),
                 "--baseline", str(arxml_factory()), "--output", str(output)])
    assert code == 2
    assert output.read_text(encoding="utf-8") == "keep"
    assert "已存在" in capsys.readouterr().out


def test_output_validation_failure_leaves_no_target_or_temp(
    workbook_factory: object, arxml_factory: object, tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "invalid_output.arxml"

    def fail_validation(*args: object, **kwargs: object) -> None:
        raise OutputValidationError("故障注入")

    monkeypatch.setattr(
        "davinci_gw.application.generate.validate_generated_output", fail_validation,
    )
    report = generate_inputs(workbook_factory(signal_rows=()), arxml_factory(), output)
    assert not report.is_success
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []
