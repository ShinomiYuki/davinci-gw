"""使用本地真实输入执行的慢速集成测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import warnings
from openpyxl import load_workbook

from davinci_gw.application.facade import GatewayFacade
from davinci_gw.application.generate import generate_inputs
from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.validate import inspect_baseline
from davinci_gw.contracts import OperationStatus, UpdateRequestDto


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "input" / "网关路由配置表_v4.84.xlsx"
BASELINE = ROOT / "input" / "825E0GA.arxml"


def fingerprint(path: Path) -> tuple[int, int, str]:
    """计算基线大小、纳秒时间戳和 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def make_add_only_copy(source: Path, target: Path) -> Path:
    """在 tmp_path 中删除 DELETE 数据行，不修改或另存真实输入。"""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Data Validation extension is not supported.*")
        workbook = load_workbook(source)
    try:
        for sheet_name in ("直接报文路由", "信号路由"):
            sheet = workbook[sheet_name]
            headers = {cell.value: cell.column for cell in sheet[1]}
            operation_column = headers["操作类型"]
            delete_rows = [row for row in range(2, sheet.max_row + 1)
                           if str(sheet.cell(row=row, column=operation_column).value or "").strip().upper() == "DELETE"]
            for row in reversed(delete_rows):
                sheet.delete_rows(row)
        workbook.save(target)
    finally:
        workbook.close()
    return target


@pytest.mark.slow
@pytest.mark.skipif(not CONFIG.exists() or not BASELINE.exists(), reason="本地真实输入不存在")
def test_real_inputs_preview_and_full_transaction_generation(tmp_path: Path) -> None:
    baseline_before = fingerprint(BASELINE)
    config_before = fingerprint(CONFIG)
    preview = preview_inputs(CONFIG, BASELINE)
    assert preview.is_valid, [issue.message for issue in preview.validation.issues]
    assert preview.target_version == "4.84"
    assert preview.reference_count == 12
    assert (preview.direct_add_count, preview.direct_delete_count) == (9, 1)
    assert (preview.signal_add_count, preview.signal_delete_count) == (2, 2)

    inspection = inspect_baseline(BASELINE)
    assert inspection.namespace_uri == "http://autosar.org/schema/r4.0"
    assert inspection.schema_filename == "AUTOSAR_00049.xsd"
    assert {name: info.definition_ref for name, info in inspection.modules.items()} == {
        "CanIf": "/MICROSAR/CanIf", "Com": "/MICROSAR/Com",
        "EcuC": "/MICROSAR/EcuC", "PduR": "/MICROSAR/PduR",
    }
    output = tmp_path / "real_full_transaction.arxml"
    facade = GatewayFacade()
    prepared = facade.prepare(UpdateRequestDto(str(CONFIG), str(BASELINE)))
    assert prepared.status is OperationStatus.SUCCESS, [issue.message for issue in prepared.issues]
    features = {item.feature_id: item for item in prepared.preview.features}
    direct = {item.key: item.value for item in features["direct_message"].metrics}
    signal = {item.key: item.value for item in features["signal_route"].metrics}
    assert (direct["requested_add"], direct["requested_delete"]) == (9, 1)
    assert (signal["requested_add"], signal["requested_delete"]) == (2, 2)
    result = facade.commit_prepared(prepared.session_id, str(output))
    assert result.status is OperationStatus.SUCCESS, [issue.message for issue in result.issues]
    assert inspect_baseline(output).schema_filename == "AUTOSAR_00049.xsd"
    assert fingerprint(BASELINE) == baseline_before
    assert fingerprint(CONFIG) == config_before


@pytest.mark.slow
@pytest.mark.skipif(not CONFIG.exists() or not BASELINE.exists(), reason="本地真实输入不存在")
def test_real_add_only_generation_skips_missing_dbc_without_stopping(tmp_path: Path) -> None:
    before = fingerprint(BASELINE)
    add_only = make_add_only_copy(CONFIG, tmp_path / "真实路由_add_only_v4.84.xlsx")
    output = tmp_path / "real_generated.arxml"
    report = generate_inputs(add_only, BASELINE, output)
    assert report.is_success, [issue.message for issue in report.all_issues]
    assert report.plan.direct_added_count + report.plan.direct_existing_count + report.plan.direct_skipped_count == 9
    assert report.plan.signal_added_count + report.plan.signal_existing_count + report.plan.signal_skipped_count == 2
    assert report.plan.signal_skipped_count == 2
    assert any("DBC" in issue.message or "ComIPdu" in issue.message for issue in report.warnings)
    assert inspect_baseline(output).schema_filename == "AUTOSAR_00049.xsd"
    assert fingerprint(BASELINE) == before
