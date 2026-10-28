"""普通直接路由不再配置 Truncation，旧端点继续按引用复用。"""

from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.models import MutationKind
from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import semantic_values
from tests.conftest import direct_row

TX_PATH = "/Cfg/CanIf/CanIfInitCfg/GWT_CanIf_DST_MSG_200_DST_CAN_Tx"


def set_truncation(path: Path, value: str | None) -> None:
    document = ArxmlDocument.load(path)
    tx = document.build_index().find_by_path(TX_PATH)[0]
    group = tx.find(f"{{{document.namespace}}}PARAMETER-VALUES")
    for entry in list(group):
        if entry.findtext(f"{{{document.namespace}}}DEFINITION-REF") == d.CANIF_TX_TRUNCATION:
            group.remove(entry)
    if value is not None:
        entry = etree.SubElement(group, f"{{{document.namespace}}}ECUC-TEXTUAL-PARAM-VALUE")
        etree.SubElement(entry, f"{{{document.namespace}}}DEFINITION-REF").text = d.CANIF_TX_TRUNCATION
        etree.SubElement(entry, f"{{{document.namespace}}}VALUE").text = value
    document.tree.write(str(path), encoding="UTF-8", xml_declaration=True)


@pytest.mark.parametrize("value", [None, "Enable", "Disable"])
def test_blank_and_legacy_switch_never_write_truncation(workbook_factory, arxml_factory, tmp_path, value):
    config = workbook_factory(signal_rows=(), direct_rows=(direct_row(**{"目标网段报文Truncation使能": value}),))
    assert read_workbook(config).is_valid
    output = tmp_path / "generated.arxml"
    report = generate_inputs(config, arxml_factory(), output)
    assert report.is_success, report.all_issues
    document = ArxmlDocument.load(output)
    tx = document.build_index().find_by_path(TX_PATH)[0]
    assert d.CANIF_TX_TRUNCATION not in semantic_values(tx, document.namespace)[0]
    # 即使真实 Tx 模板包含 true，新对象也不能继承它。
    original = document.build_index().find_by_short_name("ExistingTx")[0]
    assert semantic_values(original, document.namespace)[0][d.CANIF_TX_TRUNCATION] == ("true",)


@pytest.mark.parametrize("value", [None, "true", "false"])
def test_existing_truncation_does_not_affect_idempotence_reuse_or_delete(
    workbook_factory, arxml_factory, tmp_path, value,
):
    config = workbook_factory(signal_rows=())
    baseline = tmp_path / "existing.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    set_truncation(baseline, value)

    report = generate_inputs(config, baseline, tmp_path / "idempotent.arxml")
    assert report.is_success, report.all_issues
    assert report.plan.direct_existing_count == 1 and not report.plan.operations

    other = workbook_factory(filename="other_v4.84.xlsx", signal_rows=(), direct_rows=(direct_row(**{
        "源网段报文名称": "SRC_B", "源网段报文CANID": "0x102",
    }),))
    reused = tmp_path / "reused.arxml"
    report = generate_inputs(other, baseline, reused)
    assert report.is_success, report.all_issues
    assert report.plan.direct_added_count == 1
    assert MutationKind.CANIF_TX_PDU not in {op.kind for op in report.plan.operations}
    document = ArxmlDocument.load(reused)
    actual = semantic_values(document.build_index().find_by_path(TX_PATH)[0], document.namespace)[0]
    assert actual.get(d.CANIF_TX_TRUNCATION) == ((value,) if value else None)

    delete = workbook_factory(filename="delete_v4.84.xlsx", signal_rows=(), direct_rows=(direct_row(**{
        "操作类型": "DELETE",
    }),))
    removed = tmp_path / "deleted.arxml"
    report = generate_inputs(delete, baseline, removed)
    assert report.is_success, report.all_issues
    assert not ArxmlDocument.load(removed).build_index().find_by_path(TX_PATH)


def test_invalid_legacy_switch_still_reports_a_clear_skip(workbook_factory, arxml_factory, tmp_path):
    config = workbook_factory(signal_rows=(), direct_rows=(direct_row(**{
        "目标网段报文Truncation使能": "invalid",
    }),))
    report = generate_inputs(config, arxml_factory(), tmp_path / "invalid.arxml")
    assert report.plan.direct_skipped_count == 1
    assert any(issue.code == "DIRECT_TARGET_SWITCH_SKIPPED" for issue in report.all_issues)
