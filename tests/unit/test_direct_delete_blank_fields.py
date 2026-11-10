"""第二十二轮：DELETE 行的可空参数按契约只作身份定位，不参与参数比对。

《输入契约》写明「DELETE 行只用身份字段定位路由；其他参数允许为空」。此前
定位器把留空的 Length、报文类型、RxIndicationUL 与 Length Strategy 当成期望
字面量去比对基准现值，导致按契约留空的 DELETE 行被
DIRECT_DELETE_SOURCE_SEMANTIC_CONFLICT 阻断，删除无法进行；填写了但与基准
不一致时仍必须阻断。
"""

from __future__ import annotations

from pathlib import Path

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import autosar_path
from davinci_gw.modules import definitions as defs
from tests.conftest import direct_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _codes(report: object) -> set[str]:
    return {issue.code for issue in report.all_issues}


def _paths(output: Path, definition: str) -> set[str]:
    document = ArxmlDocument.load(output)
    index = document.build_index()
    namespace = document.namespace
    return {
        autosar_path(node, namespace)
        for node in index.find_by_definition_ref(definition)
    }


def _baseline(workbook_factory, arxml_factory, tmp_path: Path) -> Path:
    baseline_config = workbook_factory(
        filename="delete_blank_base_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_MSG", "源网段报文CANID": "0x100",
            "目标网段报文名称": "DST_MSG", "目标网段报文CANID": "0x200",
        }),),
        signal_rows=(),
    )
    baseline = tmp_path / "delete_blank_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    return baseline


def test_delete_with_blank_optional_fields_removes_only_that_route(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """留空 Length、报文类型、RxIndicationUL 与 Length Strategy 也能定位并删除。"""
    baseline = _baseline(workbook_factory, arxml_factory, tmp_path)
    config = workbook_factory(
        filename="delete_blank_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "操作类型": "DELETE",
            "源网段报文名称": "SRC_MSG", "源网段报文CANID": "0x100",
            "源网段报文Length": None, "源网段报文类型": None,
            "源网段RxIndicationUL": None, "源网段报文Dlc Check使能": None,
            "目标网段报文名称": "DST_MSG", "目标网段报文CANID": "0x200",
            "目标网段报文Length": None, "目标网段报文类型": None,
            "路由Length Strategy功能选择": None,
        }),),
        signal_rows=(),
    )
    output = tmp_path / "delete_blank_output.arxml"

    report = generate_inputs(config, baseline, output)

    assert report.is_success, _messages(report)
    assert "DIRECT_DELETE_SOURCE_SEMANTIC_CONFLICT" not in _codes(report)
    paths = _paths(output, defs.PDUR_PATH)
    assert not any("SRC_MSG_100_SRC" in path for path in paths), paths
    assert any("ExistingPath" in path for path in paths), paths
    destinations = _paths(output, defs.PDUR_DEST)
    assert not any("DST_MSG_200_DST" in path for path in destinations), destinations
    assert any("ExistingDest" in path for path in destinations), destinations


def test_delete_with_length_conflicting_baseline_is_still_blocked(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """填写了与基准不一致的 Length 时仍必须阻断，不能因为放宽而放过。"""
    baseline = _baseline(workbook_factory, arxml_factory, tmp_path)
    config = workbook_factory(
        filename="delete_conflict_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "操作类型": "DELETE",
            "源网段报文名称": "SRC_MSG", "源网段报文CANID": "0x100",
            "源网段报文Length": 16,
            "目标网段报文名称": "DST_MSG", "目标网段报文CANID": "0x200",
        }),),
        signal_rows=(),
    )
    output = tmp_path / "delete_conflict_output.arxml"

    report = generate_inputs(config, baseline, output)

    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_DELETE_SOURCE_SEMANTIC_CONFLICT" in _codes(report)


def test_add_still_requires_and_validates_optional_fields(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """ADD 侧这些字段仍是必填，放宽只作用于 DELETE 的定位比对。"""
    config = workbook_factory(
        filename="add_blank_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文Length": None,
        }),),
        signal_rows=(),
    )
    output = tmp_path / "add_blank_output.arxml"

    report = generate_inputs(config, arxml_factory(), output)

    assert not report.is_success
    assert not output.exists()
    assert any("WORKBOOK" in code or "REQUIRED" in code for code in _codes(report)), _codes(report)
