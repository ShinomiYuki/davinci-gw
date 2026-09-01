"""第08轮动态映射缺组阻断与旧命名语义定位补充测试。"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.models import (
    MutationAction,
    ReferenceDataEntry,
    SourceLocation,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref
from davinci_gw.routing.routing_group_membership import RoutingGroupMembershipService
from davinci_gw.routing.delete import _message_identity_matches
from tests.conftest import direct_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _rename_generated_chain_to_gw(path: Path) -> None:
    """同步替换对象短名与引用路径，模拟 T13J 中已存在的 Gw_ 旧命名。"""
    tree = etree.parse(str(path))
    for node in tree.getroot().iter():
        if node.text and "GWT_" in node.text:
            node.text = node.text.replace("GWT_", "Gw_")
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def _add_buffer(path: Path, name: str) -> None:
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    buffer = next(node for node in tree.getroot().iter()
                  if definition_ref(node, namespace) == defs.CANIF_BUFFER)
    clone = etree.fromstring(etree.tostring(buffer))
    clone.find(f"{{{namespace}}}SHORT-NAME").text = name
    clone.set("UUID", "00000000-0000-0000-0000-999999999997")
    buffer.getparent().append(clone)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def _set_reference(
    node: etree._Element, namespace: str, definition: str, target: str,
) -> None:
    values = node.find(f"{{{namespace}}}REFERENCE-VALUES")
    entry = next(item for item in values
                 if item.findtext(f"{{{namespace}}}DEFINITION-REF") == definition)
    entry.find(f"{{{namespace}}}VALUE-REF").text = target


def _add_off_channel_member(path: Path, *, add_second_main: bool) -> None:
    """给默认组增加一个 SRC_CAN 偏离成员，可选再补一个 DST_CAN 主通道成员。"""
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    ecuc = next(node for node in tree.getroot().iter()
                if node.findtext(f"{{{namespace}}}SHORT-NAME") == "ExistingPdu_Rx")
    ecuc_clone = etree.fromstring(etree.tostring(ecuc))
    ecuc_clone.find(f"{{{namespace}}}SHORT-NAME").text = "OffChannelPdu"
    ecuc.getparent().append(ecuc_clone)
    off_pdu_path = "/Cfg/EcuC/EcucPduCollection/OffChannelPdu"

    tx = next(node for node in tree.getroot().iter()
              if node.findtext(f"{{{namespace}}}SHORT-NAME") == "ExistingTx")
    tx_clone = etree.fromstring(etree.tostring(tx))
    tx_clone.find(f"{{{namespace}}}SHORT-NAME").text = "OffChannelTx"
    _set_reference(tx_clone, namespace, defs.CANIF_TX_BUFFER_REF, "/Cfg/CanIf/CanIfInitCfg/TX")
    _set_reference(tx_clone, namespace, defs.CANIF_TX_PDU_REF, off_pdu_path)
    tx.getparent().append(tx_clone)

    destination = next(node for node in tree.getroot().iter()
                       if node.findtext(f"{{{namespace}}}SHORT-NAME") == "ExistingDest")
    off_destination = etree.fromstring(etree.tostring(destination))
    off_destination.find(f"{{{namespace}}}SHORT-NAME").text = "OffChannelDest"
    _set_reference(off_destination, namespace, defs.PDUR_DEST_PDU_REF, off_pdu_path)
    destination.getparent().append(off_destination)
    off_path = (
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/ExistingPath/OffChannelDest"
    )
    group = next(node for node in tree.getroot().iter()
                 if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP)
    reference_values = group.find(f"{{{namespace}}}REFERENCE-VALUES")
    off_reference = etree.fromstring(etree.tostring(reference_values[0]))
    off_reference.find(f"{{{namespace}}}VALUE-REF").text = off_path
    reference_values.append(off_reference)

    if add_second_main:
        main_destination = etree.fromstring(etree.tostring(destination))
        main_destination.find(f"{{{namespace}}}SHORT-NAME").text = "ExistingDestSecond"
        destination.getparent().append(main_destination)
        main_reference = etree.fromstring(etree.tostring(reference_values[0]))
        main_reference.find(f"{{{namespace}}}VALUE-REF").text = (
            "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/ExistingPath/ExistingDestSecond"
        )
        reference_values.append(main_reference)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def _service(path: Path) -> RoutingGroupMembershipService:
    location = SourceLocation("引用数据", 2)
    return RoutingGroupMembershipService(
        ArxmlDocument.load(path),
        reference_data=(
            ReferenceDataEntry("SRC_CAN", "TX", "HRH", location),
            ReferenceDataEntry("DST_CAN", "TX2", "HRH2", location),
        ),
    )


def _add_unrouted_self_transmit(path: Path) -> None:
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    target_ecuc = next(node for node in tree.getroot().iter()
                       if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_DST_MSG_DST_Tx"
                       and definition_ref(node, namespace) == defs.ECUC_PDU)
    ecuc_clone = etree.fromstring(etree.tostring(target_ecuc))
    ecuc_clone.find(f"{{{namespace}}}SHORT-NAME").text = "LocalSelfPdu"
    target_ecuc.getparent().append(ecuc_clone)
    target_tx = next(node for node in tree.getroot().iter()
                     if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_DST_MSG_DST_Tx"
                     and definition_ref(node, namespace) == defs.CANIF_TX)
    tx_clone = etree.fromstring(etree.tostring(target_tx))
    tx_clone.find(f"{{{namespace}}}SHORT-NAME").text = "GWH_DST_MSG_LocalSelfTx"
    _set_reference(
        tx_clone, namespace, defs.CANIF_TX_PDU_REF,
        "/Cfg/EcuC/EcucPduCollection/LocalSelfPdu",
    )
    target_tx.getparent().append(tx_clone)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def test_delete_semantically_locates_old_gw_chain(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = tmp_path / "old_gw_base.arxml"
    add_report = generate_inputs(
        workbook_factory(filename="old_gw_add_v4.84.xlsx", signal_rows=()),
        arxml_factory(),
        baseline,
    )
    assert add_report.is_success, _messages(add_report)
    _rename_generated_chain_to_gw(baseline)

    output = tmp_path / "old_gw_output.arxml"
    delete_report = generate_inputs(
        workbook_factory(
            filename="old_gw_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ),
        baseline,
        output,
    )
    assert delete_report.is_success, _messages(delete_report)
    actions = [operation.action for operation in delete_report.plan.operations]
    assert actions.index(MutationAction.REMOVE_REFERENCE) < actions.index(MutationAction.REMOVE)
    assert any("Gw_SRC_MSG_100_SRC" in operation.object_path
               for operation in delete_report.plan.operations)


def test_message_name_boundary_is_prefix_independent() -> None:
    assert _message_identity_matches("GWH_ABC_1_DST_Tx", "ABC_1")
    assert _message_identity_matches("Gw_ABC_1_DST_Tx", "ABC_1")
    assert _message_identity_matches("ABC_1_DST_Tx", "ABC_1")
    assert not _message_identity_matches("GWH_ABC_10_DST_Tx", "ABC_1")


def test_delete_ignores_unrouted_self_transmit(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = tmp_path / "self_transmit_base.arxml"
    add_report = generate_inputs(
        workbook_factory(filename="self_transmit_add_v4.84.xlsx", signal_rows=()),
        arxml_factory(), baseline,
    )
    assert add_report.is_success, _messages(add_report)
    _add_unrouted_self_transmit(baseline)
    output = tmp_path / "self_transmit_output.arxml"
    delete_report = generate_inputs(
        workbook_factory(
            filename="self_transmit_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ),
        baseline,
        output,
    )
    assert delete_report.is_success, _messages(delete_report)
    document = ArxmlDocument.load(output)
    assert document.build_index().find_by_short_name("GWH_DST_MSG_LocalSelfTx")


def test_target_channel_without_application_group_is_blocked(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = arxml_factory(filename="missing_application_group.arxml")
    _add_buffer(baseline, "TX3")
    references = (
        {"CAN通道名称": "SRC_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH"},
        {"CAN通道名称": "DST_CAN", "CanIfTxBuffer名称": "TX2", "CanIfHrh名称": "HRH2"},
        {"CAN通道名称": "DGCAN", "CanIfTxBuffer名称": "TX3", "CanIfHrh名称": "HRH2"},
    )
    output = tmp_path / "missing_application_group_output.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="missing_application_group_v4.84.xlsx",
            direct_rows=(direct_row(**{"目标网段CAN通道": "DGCAN"}),),
            signal_rows=(), reference_rows=references,
        ),
        baseline,
        output,
    )
    assert not report.is_success and not output.exists()
    assert "PDUR_APPLICATION_ROUTING_GROUP_NOT_FOUND" in {issue.code for issue in report.errors}
    assert "DGCAN" in _messages(report) and "诊断组" in _messages(report)


def test_group_main_channel_tie_is_blocked(arxml_factory) -> None:
    baseline = arxml_factory(filename="main_channel_tie.arxml")
    _add_off_channel_member(baseline, add_second_main=False)
    mapping, problems = _service(baseline).application_group_mapping()
    assert "DST_CAN" not in mapping and "SRC_CAN" not in mapping
    assert "PDUR_ROUTING_GROUP_MAIN_CHANNEL_AMBIGUOUS" in {
        problem.code for problem in problems if not problem.warning
    }


def test_non_main_member_only_warns_and_does_not_pollute_mapping(arxml_factory) -> None:
    baseline = arxml_factory(filename="non_main_member.arxml")
    _add_off_channel_member(baseline, add_second_main=True)
    mapping, problems = _service(baseline).application_group_mapping()
    assert mapping["DST_CAN"][0] == "DefaultRoutingGroup"
    assert "SRC_CAN" not in mapping
    warnings = [problem for problem in problems if problem.warning]
    assert len(warnings) == 1
    assert "OffChannelDest" in warnings[0].message and "SRC_CAN" in warnings[0].message
