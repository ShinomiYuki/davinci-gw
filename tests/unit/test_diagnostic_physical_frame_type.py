"""第二十四轮：物理寻址诊断的端点定位必须按通道属性区分 CAN 类型。

同一 CAN 通道与 CAN ID 上并存经典与 FD 两个诊断透传端点，是真实工程的常见结构
（客户真实基线的 DGCAN 0x734/0x7B4 即是如此）。功能寻址早已按通道属性筛选
帧，物理寻址没有，会把并存端点误报成「CanIf 端点不完整或歧义」；用户在配置表
侧无法消除另一个端点，只能被阻断。
"""

from __future__ import annotations

from copy import deepcopy

from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import semantic_values
from davinci_gw.routing.transaction import TransactionCoordinator
from tests.unit.test_diagnostic_routes import diagnostic_case  # noqa: F401


def _clone_rx(document, can_id: str, *, can_id_type: str, dlc: str, short_name: str) -> None:
    """在同一 HRH 上复制一个同 CAN ID 的接收端点，并改写 CAN 类型与 DLC。"""
    index = document.build_index()
    namespace = document.namespace
    source = next(
        node for node in index.find_by_definition_ref(d.CANIF_RX)
        if semantic_values(node, namespace)[0].get(d.CANIF_RX_CAN_ID) == (can_id,)
    )
    clone = deepcopy(source)
    clone.find(f"{{{namespace}}}SHORT-NAME").text = short_name
    for entry in clone.find(f"{{{namespace}}}PARAMETER-VALUES"):
        definition = entry.findtext(f"{{{namespace}}}DEFINITION-REF")
        if definition == d.CANIF_RX_CAN_ID_TYPE:
            entry.find(f"{{{namespace}}}VALUE").text = can_id_type
        elif definition == d.CANIF_RX_DLC:
            entry.find(f"{{{namespace}}}VALUE").text = dlc
    source.getparent().append(clone)


def _frames_on_same_can_id(document, can_id: str) -> list[str]:
    index = document.build_index()
    namespace = document.namespace
    names = []
    for node in index.find_by_definition_ref(d.CANIF_RX):
        parameters, _ = semantic_values(node, namespace)
        if parameters.get(d.CANIF_RX_CAN_ID) == (can_id,):
            if parameters.get(d.CANIF_RX_INDICATION_UL) == ("CAN_TP",):
                names.append(node.findtext(f"{{{namespace}}}SHORT-NAME") or "")
    return names


def test_physical_diagnostic_ignores_frame_with_other_can_type(diagnostic_case):
    """同 CAN ID 上并存另一个 CAN 类型的端点时，按通道属性选中的那个仍能定位。"""
    document, workbook, _, _ = diagnostic_case
    _clone_rx(document, "2016", can_id_type="STANDARD_NO_FD_CAN", dlc="8",
              short_name="FrameSRC_CANRxClassic")
    assert len(_frames_on_same_can_id(document, "2016")) == 2

    plan = TransactionCoordinator(document, workbook).plan_and_apply()

    assert not plan.errors, [issue.message for issue in plan.issues]
    assert plan.diagnostic_existing_count == 1
    assert not plan.operations


def test_physical_diagnostic_still_blocks_same_can_type_duplicates(diagnostic_case):
    """同 CAN ID 上并存同 CAN 类型的端点仍是真歧义，必须继续阻断。"""
    document, workbook, _, _ = diagnostic_case
    _clone_rx(document, "2016", can_id_type="STANDARD_FD_CAN", dlc="64",
              short_name="FrameSRC_CANRxDuplicate")

    plan = TransactionCoordinator(document, workbook).plan_and_apply()

    assert plan.errors
    assert any("歧义" in issue.message for issue in plan.issues), [
        issue.message for issue in plan.issues
    ]
