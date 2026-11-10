"""第二十一轮：源端语义定位与目标端同口径。

同一 CAN 通道与 CAN ID 上可以并存多个报文名不同的接收端点（真实基线里诊断透传
报文即在同一 HRH 与 CAN ID 上同时存在经典与 FD 两个端点）。源端定位器此前把
「报文名不匹配」也判成冲突，会把合法新增阻断成 DIRECT_SOURCE_CHAIN_CONFLICT；
目标端定位器早已按报文名区分。这里锁定源端与目标端的一致口径。
"""

from __future__ import annotations

from pathlib import Path

from davinci_gw.application.generate import generate_inputs
from davinci_gw.domain.models import MutationKind
from tests.conftest import direct_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _codes(report: object) -> set[str]:
    return {issue.code for issue in report.all_issues}


def test_add_allows_different_message_with_same_source_can_identity(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """不同报文名可共享源 CAN ID 与通道，不能误报成源端点占用。"""
    baseline_config = workbook_factory(
        filename="same_source_id_base_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_A",
        }),),
        signal_rows=(),
    )
    baseline = tmp_path / "same_source_id_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="same_source_id_new_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_B", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_B", "目标网段报文CANID": "0x201",
        }),),
        signal_rows=(),
    )

    report = generate_inputs(config, baseline, tmp_path / "same_source_id_output.arxml")

    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 1
    kinds = {operation.kind for operation in report.plan.operations}
    assert MutationKind.CANIF_RX_PDU in kinds
    assert MutationKind.PDUR_SRC_PDU in kinds
    assert "DIRECT_SOURCE_CHAIN_CONFLICT" not in _codes(report)


def test_add_reuses_named_source_when_same_can_id_has_other_message(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """同一 CAN ID 上并存不同报文名时，按报文名命中的既有源链仍应被复用。"""
    baseline_config = workbook_factory(
        filename="shared_source_id_base_v4.84.xlsx",
        direct_rows=(
            direct_row(**{
                "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
                "目标网段报文名称": "TARGET_A",
            }),
            direct_row(**{
                "源网段报文名称": "SRC_B", "源网段报文CANID": "0x101",
                "目标网段报文名称": "TARGET_B", "目标网段报文CANID": "0x201",
            }),
        ),
        signal_rows=(),
    )
    baseline = tmp_path / "shared_source_id_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="shared_source_id_new_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_C", "目标网段报文CANID": "0x202",
        }),),
        signal_rows=(),
    )

    report = generate_inputs(config, baseline, tmp_path / "shared_source_id_output.arxml")

    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 1
    kinds = {operation.kind for operation in report.plan.operations}
    assert MutationKind.PDUR_DEST_PDU in kinds
    assert MutationKind.CANIF_RX_PDU not in kinds
    assert MutationKind.PDUR_SRC_PDU not in kinds
    assert "DIRECT_SOURCE_CHAIN_CONFLICT" not in _codes(report)


def test_add_warns_when_source_name_missing_but_can_id_has_other_endpoint(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """报文名查不到但同 CAN ID 上有别的端点时，给出可定位的提示而不是静默。"""
    baseline_config = workbook_factory(
        filename="shared_source_warn_base_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_A",
        }),),
        signal_rows=(),
    )
    baseline = tmp_path / "shared_source_warn_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="shared_source_warn_new_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_OTHER", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_B", "目标网段报文CANID": "0x201",
        }),),
        signal_rows=(),
    )

    report = generate_inputs(config, baseline, tmp_path / "shared_source_warn_output.arxml")

    assert report.is_success, _messages(report)
    assert "DIRECT_SOURCE_SHARED_CAN_ID_ENDPOINT" in _codes(report)
