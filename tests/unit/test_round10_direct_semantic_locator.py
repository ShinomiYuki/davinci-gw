"""第10轮直接报文语义定位、端点复用和自发报文隔离测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import autosar_path
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, semantic_values
from davinci_gw.domain.models import MutationKind
from tests.conftest import direct_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _named(
    tree: etree._ElementTree, namespace: str, short_name: str, definition: str,
) -> etree._Element:
    return next(
        node for node in tree.getroot().iter()
        if node.findtext(f"{{{namespace}}}SHORT-NAME") == short_name
        and definition_ref(node, namespace) == definition
    )


def _set_parameter(
    node: etree._Element, namespace: str, definition: str, value: str,
) -> None:
    parameters = node.find(f"{{{namespace}}}PARAMETER-VALUES")
    entry = next(
        item for item in parameters
        if item.findtext(f"{{{namespace}}}DEFINITION-REF") == definition
    )
    entry.find(f"{{{namespace}}}VALUE").text = value


def _rename_generated_objects(path: Path, *, opaque_destination: bool = False) -> str:
    """同步更新短名和完整引用，模拟不遵循当前工具命名的历史路由。"""
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    for node in tree.getroot().iter():
        if node.text and "GWT_" in node.text:
            node.text = node.text.replace("GWT_", "Legacy_")
    destination_path = (
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/Legacy_SRC_MSG_100_SRC/"
        "DST_MSG_200_DST"
    )
    if opaque_destination:
        destination = _named(tree, namespace, "DST_MSG_200_DST", defs.PDUR_DEST)
        destination.find(f"{{{namespace}}}SHORT-NAME").text = "OpaqueLeg"
        renamed = destination_path.rsplit("/", 1)[0] + "/OpaqueLeg"
        for value_ref in tree.getroot().iter(f"{{{namespace}}}VALUE-REF"):
            if value_ref.text == destination_path:
                value_ref.text = renamed
        destination_path = renamed
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)
    return destination_path


def _leave_only_local_target_endpoint(path: Path) -> None:
    """移除完整网关路由和源端，只保留同总线身份的本地 Tx/EcuC。"""
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    routing_path = _named(tree, namespace, "GWT_SRC_MSG_100_SRC", defs.PDUR_PATH)
    destination_path = autosar_path(
        _named(tree, namespace, "DST_MSG_200_DST", defs.PDUR_DEST), namespace,
    )
    for group in tree.getroot().iter():
        if definition_ref(group, namespace) != defs.PDUR_ROUTING_GROUP:
            continue
        references = group.find(f"{{{namespace}}}REFERENCE-VALUES")
        for reference in list(references) if references is not None else ():
            if reference.findtext(f"{{{namespace}}}VALUE-REF") == destination_path:
                references.remove(reference)
    routing_path.getparent().remove(routing_path)
    for short_name, definition in (
        ("GWT_SRC_MSG_SRC_Rx", defs.CANIF_RX),
        ("GWT_SRC_MSG_SRC_Rx", defs.ECUC_PDU),
    ):
        node = _named(tree, namespace, short_name, definition)
        node.getparent().remove(node)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def _leave_only_local_source_endpoint(path: Path) -> None:
    """移除 PduR 路由和目标端，只保留没有 PduRSrcPdu 的本地 Rx/EcuC。"""
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    routing_path = _named(tree, namespace, "GWT_SRC_MSG_100_SRC", defs.PDUR_PATH)
    destination_path = autosar_path(
        _named(tree, namespace, "DST_MSG_200_DST", defs.PDUR_DEST), namespace,
    )
    for group in tree.getroot().iter():
        if definition_ref(group, namespace) != defs.PDUR_ROUTING_GROUP:
            continue
        references = group.find(f"{{{namespace}}}REFERENCE-VALUES")
        for reference in list(references) if references is not None else ():
            if reference.findtext(f"{{{namespace}}}VALUE-REF") == destination_path:
                references.remove(reference)
    routing_path.getparent().remove(routing_path)
    for short_name, definition in (
        ("GWT_DST_MSG_DST_Tx", defs.CANIF_TX),
        ("GWT_DST_MSG_DST_Tx", defs.ECUC_PDU),
    ):
        node = _named(tree, namespace, short_name, definition)
        node.getparent().remove(node)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def _duplicate_complete_path(path: Path) -> None:
    """制造第二条完整源链，验证污染文件不会被继续追加第三套对象。"""
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    original = _named(tree, namespace, "GWT_SRC_MSG_100_SRC", defs.PDUR_PATH)
    clone = deepcopy(original)
    clone.find(f"{{{namespace}}}SHORT-NAME").text = "SecondCompletePath"
    original.getparent().append(clone)
    group = next(
        node for node in tree.getroot().iter()
        if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP
    )
    references = group.find(f"{{{namespace}}}REFERENCE-VALUES")
    assert references is not None
    member = deepcopy(references[0])
    member.find(f"{{{namespace}}}VALUE-REF").text = (
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/SecondCompletePath/DST_MSG_200_DST"
    )
    references.append(member)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def test_add_recognizes_arbitrary_existing_names_and_opaque_destination(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    baseline = tmp_path / "legacy.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    _rename_generated_objects(baseline, opaque_destination=True)

    output = tmp_path / "legacy_output.arxml"
    report = generate_inputs(config, baseline, output)
    assert report.is_success, _messages(report)
    assert (report.plan.direct_added_count, report.plan.direct_existing_count) == (0, 1)
    assert report.plan.operations == ()


def test_delete_uses_reference_chain_when_destination_name_has_no_message(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    add_config = workbook_factory(filename="opaque_add_v4.84.xlsx", signal_rows=())
    baseline = tmp_path / "opaque_delete_base.arxml"
    assert generate_inputs(add_config, arxml_factory(), baseline).is_success
    destination_path = _rename_generated_objects(baseline, opaque_destination=True)

    delete_config = workbook_factory(
        filename="opaque_delete_v4.84.xlsx",
        direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
    )
    output = tmp_path / "opaque_delete_output.arxml"
    report = generate_inputs(delete_config, baseline, output)
    assert report.is_success, _messages(report)
    assert not ArxmlDocument.load(output).build_index().find_by_path(destination_path)


def test_add_reuses_existing_source_path_for_new_destination(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = tmp_path / "source_reuse_base.arxml"
    assert generate_inputs(workbook_factory(signal_rows=()), arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="source_reuse_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "目标网段报文名称": "DST_MSG_2", "目标网段报文CANID": "0x201",
        }),), signal_rows=(),
    )
    report = generate_inputs(config, baseline, tmp_path / "source_reuse_output.arxml")
    assert report.is_success, _messages(report)
    kinds = {operation.kind for operation in report.plan.operations}
    assert report.plan.direct_added_count == 1
    assert MutationKind.PDUR_DEST_PDU in kinds
    assert MutationKind.CANIF_TX_PDU in kinds
    assert MutationKind.CANIF_RX_PDU not in kinds
    assert MutationKind.PDUR_ROUTING_PATH not in kinds
    assert MutationKind.PDUR_SRC_PDU not in kinds


def test_add_reuses_target_endpoint_with_application_pdur_evidence(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline_config = workbook_factory(
        filename="target_reuse_base_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
        }),), signal_rows=(),
    )
    baseline = tmp_path / "target_reuse_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="target_reuse_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_B", "源网段报文CANID": "0x102",
        }),), signal_rows=(),
    )
    report = generate_inputs(config, baseline, tmp_path / "target_reuse_output.arxml")
    assert report.is_success, _messages(report)
    kinds = [operation.kind for operation in report.plan.operations]
    assert report.plan.direct_added_count == 1
    assert MutationKind.PDUR_DEST_PDU in kinds
    assert MutationKind.CANIF_TX_PDU not in kinds
    assert kinds.count(MutationKind.ECUC_PDU) == 1


def test_add_allows_different_message_with_same_target_can_identity(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """不同报文名可共享目标 CAN ID/TxBuffer，不能误报成本地自发端点占用。"""
    baseline_config = workbook_factory(
        filename="same_id_base_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_A", "源网段报文CANID": "0x101",
            "目标网段报文名称": "TARGET_A",
        }),), signal_rows=(),
    )
    baseline = tmp_path / "same_id_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    config = workbook_factory(
        filename="same_id_new_v4.84.xlsx",
        direct_rows=(direct_row(**{
            "源网段报文名称": "SRC_B", "源网段报文CANID": "0x102",
            "目标网段报文名称": "TARGET_B",
        }),), signal_rows=(),
    )

    report = generate_inputs(config, baseline, tmp_path / "same_id_output.arxml")
    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 1
    assert MutationKind.CANIF_TX_PDU in {operation.kind for operation in report.plan.operations}


def test_add_blocks_local_self_transmit_endpoint_without_pdur_evidence(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    baseline = tmp_path / "local_tx_base.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    _leave_only_local_target_endpoint(baseline)

    output = tmp_path / "local_tx_output.arxml"
    report = generate_inputs(config, baseline, output)
    assert not report.is_success and not output.exists()
    assert "DIRECT_TARGET_ENDPOINT_CONFLICT" in {issue.code for issue in report.errors}
    assert "本地自发/COM" in _messages(report)


def test_add_uses_existing_source_can_type_when_only_new_target_leg_is_added(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """完整既有 Rx 的 CAN 类型以 ARXML 为准，新增目标腿不得回写源端。"""
    baseline_config = workbook_factory(filename="source_type_base_v4.84.xlsx", signal_rows=())
    baseline = tmp_path / "source_type_base.arxml"
    assert generate_inputs(baseline_config, arxml_factory(), baseline).is_success
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    source_rx = _named(tree, namespace, "GWT_SRC_MSG_SRC_Rx", defs.CANIF_RX)
    _set_parameter(source_rx, namespace, defs.CANIF_RX_CAN_ID_TYPE, "STANDARD_FD_CAN")
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    new_leg = direct_row(**{
        "目标网段报文名称": "DST_MSG_2",
        "目标网段报文CANID": "0x201",
    })
    output = tmp_path / "source_type_output.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="source_type_new_v4.84.xlsx",
            direct_rows=(new_leg,),
            signal_rows=(),
        ),
        baseline,
        output,
    )

    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 1
    assert "DIRECT_SOURCE_TYPE_FROM_BASELINE" in {
        issue.code for issue in report.warnings
    }
    generated = ArxmlDocument.load(output)
    generated_source = _named(
        etree.ElementTree(generated.root), generated.namespace,
        "GWT_SRC_MSG_SRC_Rx", defs.CANIF_RX,
    )
    parameters, _ = semantic_values(generated_source, generated.namespace)
    assert parameters[defs.CANIF_RX_CAN_ID_TYPE] == ("STANDARD_FD_CAN",)


def test_delete_keeps_strict_source_can_type_matching(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """采用基准源类型只属于 ADD；DELETE 仍须按原参数精确定位旧路由。"""
    config = workbook_factory(filename="strict_delete_base_v4.84.xlsx", signal_rows=())
    baseline = tmp_path / "strict_delete_base.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    source_rx = _named(tree, namespace, "GWT_SRC_MSG_SRC_Rx", defs.CANIF_RX)
    _set_parameter(source_rx, namespace, defs.CANIF_RX_CAN_ID_TYPE, "STANDARD_FD_CAN")
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "strict_delete_output.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="strict_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),),
            signal_rows=(),
        ),
        baseline,
        output,
    )

    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_DELETE_SOURCE_SEMANTIC_CONFLICT" in {
        issue.code for issue in report.errors
    }


def test_add_does_not_accept_invalid_existing_source_can_type(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """基准优先仅适用于受支持的 CAN 类型，损坏枚举不能被静默接纳。"""
    config = workbook_factory(filename="invalid_source_type_base_v4.84.xlsx", signal_rows=())
    baseline = tmp_path / "invalid_source_type_base.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    source_rx = _named(tree, namespace, "GWT_SRC_MSG_SRC_Rx", defs.CANIF_RX)
    _set_parameter(source_rx, namespace, defs.CANIF_RX_CAN_ID_TYPE, "BROKEN_CAN_TYPE")
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "invalid_source_type_output.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="invalid_source_type_v4.84.xlsx",
            direct_rows=(direct_row(**{
                "目标网段报文名称": "DST_MSG_2",
                "目标网段报文CANID": "0x201",
            }),),
            signal_rows=(),
        ),
        baseline,
        output,
    )

    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_SOURCE_CHAIN_CONFLICT" in {
        issue.code for issue in report.errors
    }


def test_add_blocks_local_receive_endpoint_without_pdur_source_chain(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    baseline = tmp_path / "local_rx_base.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    _leave_only_local_source_endpoint(baseline)

    output = tmp_path / "local_rx_output.arxml"
    report = generate_inputs(config, baseline, output)
    assert not report.is_success and not output.exists()
    assert "DIRECT_SOURCE_CHAIN_CONFLICT" in {issue.code for issue in report.errors}
    assert "没有完整的 CanIf PduRSrcPdu 源链" in _messages(report)


def test_add_blocks_duplicate_complete_source_chains(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    baseline = tmp_path / "duplicate_chain_base.arxml"
    assert generate_inputs(config, arxml_factory(), baseline).is_success
    _duplicate_complete_path(baseline)

    output = tmp_path / "duplicate_chain_output.arxml"
    report = generate_inputs(config, baseline, output)
    assert not report.is_success and not output.exists()
    assert "DIRECT_SOURCE_CHAIN_AMBIGUOUS" in {issue.code for issue in report.errors}
    assert "SecondCompletePath" in _messages(report)
