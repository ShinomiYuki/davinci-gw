"""第03轮直接报文/信号 DELETE、共享保留、替换与事务测试。"""

from __future__ import annotations

from pathlib import Path
import hashlib

from lxml import etree
import pytest

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, semantic_values
from davinci_gw.domain.errors import ArxmlStructureError, OutputValidationError
from tests.conftest import direct_row, signal_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _delete_direct(**changes: object) -> dict[str, object]:
    return direct_row(**{"操作类型": "DELETE", **changes})


def _delete_signal(**changes: object) -> dict[str, object]:
    return signal_row(**{"操作类型": "DELETE", **changes})


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _append_reference_to_shared_node(path: Path, target: str) -> None:
    tree = etree.parse(str(path))
    namespace = etree.QName(tree.getroot()).namespace
    entry = next(node for node in tree.getroot().iter()
                 if node.findtext(f"{{{namespace}}}DEFINITION-REF") == "/Defs/Reference")
    entry.find(f"{{{namespace}}}VALUE-REF").text = target
    tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def test_delete_single_direct_route_cleans_complete_owned_chain(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    output = tmp_path / "direct_deleted.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.direct_deleted_count == 1
    index = ArxmlDocument.load(output).build_index()
    removed = (
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC",
        "/Cfg/CanIf/CanIfInitCfg/GWT_SRC_MSG_SRC_Rx",
        "/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_DST_Tx",
        "/Cfg/EcuC/EcucPduCollection/GWT_SRC_MSG_SRC_Rx",
        "/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_DST_Tx",
    )
    assert all(not index.find_by_path(path) for path in removed)
    assert len(index.find_by_path("/Cfg/CanIf/CanIfInitCfg/CanIfInitHohCfg/HRH")) == 1
    assert len(index.find_by_path("/Cfg/CanIf/CanIfInitCfg/TX2")) == 1


def test_direct_delete_is_idempotent_when_route_is_absent(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()),
        arxml_factory(), tmp_path / "direct_missing.arxml",
    )
    assert report.is_success
    assert report.plan.direct_missing_count == 1
    assert "已不存在" in _messages(report)


def test_signal_delete_is_idempotent_when_dbc_endpoint_and_mapping_are_absent(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    """DBC 已移除源端且没有遗留 Mapping 时，DELETE 应安全视为已不存在。"""
    baseline = arxml_factory(filename="signal_dbc_missing.arxml")
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    for node in tuple(tree.getroot().iter()):
        if definition_ref(node, namespace) not in {defs.COM_IPDU, defs.COM_SIGNAL}:
            continue
        name = node.findtext(f"{{{namespace}}}SHORT-NAME") or ""
        if name in {"SRC_MSG_oSRC_Rx", "SRC_SIG_oSRC_MSG_oSRC_Rx"}:
            node.getparent().remove(node)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "signal_dbc_missing_output.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )

    assert report.is_success, _messages(report)
    assert output.exists()
    assert report.plan.signal_missing_count == 1
    assert "相关 ComGwMapping 引用均已不存在" in _messages(report)


def test_signal_delete_blocks_when_missing_dbc_endpoint_has_dangling_mapping_reference(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    """端点被删但 Mapping 仍引用旧信号时必须阻断，不能把悬空路由当作幂等成功。"""
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    for node in tuple(tree.getroot().iter()):
        if definition_ref(node, namespace) not in {defs.COM_IPDU, defs.COM_SIGNAL}:
            continue
        name = node.findtext(f"{{{namespace}}}SHORT-NAME") or ""
        if name in {"SRC_MSG_oSRC_Rx", "SRC_SIG_oSRC_MSG_oSRC_Rx"}:
            node.getparent().remove(node)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "signal_dangling_mapping_output.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )

    assert not report.is_success
    assert not output.exists()
    assert "SIGNAL_DELETE_DANGLING_REFERENCE" in {
        issue.code for issue in report.errors
    }


def test_one_to_many_direct_delete_keeps_other_leg_and_source_chain(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    second = direct_row(**{"目标网段报文名称": "DST_MSG_2", "目标网段报文CANID": "0x201"})
    baseline = generated_arxml_factory(direct_rows=(direct_row(), second))
    output = tmp_path / "direct_one_leg_deleted.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    path = "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC"
    assert len(index.find_by_path(path)) == 1
    assert not index.find_by_path(f"{path}/DST_MSG_200_DST")
    assert len(index.find_by_path(f"{path}/DST_MSG_2_201_DST")) == 1
    assert len(index.find_by_path("/Cfg/CanIf/CanIfInitCfg/GWT_SRC_MSG_SRC_Rx")) == 1
    retained_paths = {
        operation.object_path for operation in report.plan.operations
        if operation.action.value == "RETAIN"
    }
    assert "/Cfg/EcuC/EcucPduCollection/GWT_SRC_MSG_SRC_Rx" in retained_paths
    assert "/Cfg/CanIf/CanIfInitCfg/GWT_SRC_MSG_SRC_Rx" in retained_paths
    assert "/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_2_DST_Tx" in retained_paths
    assert "/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_2_DST_Tx" in retained_paths
    assert report.plan.direct_retained_count >= 1


def test_shared_target_objects_are_retained_by_other_routing_path(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    references = (
        {"CAN通道名称": "SRC_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH"},
        {"CAN通道名称": "SRC2_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH2"},
        {"CAN通道名称": "DST_CAN", "CanIfTxBuffer名称": "TX2", "CanIfHrh名称": "HRH2"},
    )
    other_source = direct_row(**{
        "源网段报文名称": "SRC_MSG_2", "源网段报文CANID": "0x101",
        "源网段CAN通道": "SRC2_CAN",
    })
    baseline = generated_arxml_factory(
        direct_rows=(direct_row(), other_source), reference_rows=references,
    )
    output = tmp_path / "shared_target.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(_delete_direct(),), signal_rows=(), reference_rows=references,
        ), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    assert len(index.find_by_path("/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_DST_Tx")) == 1
    assert len(index.find_by_path("/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_DST_Tx")) == 1
    assert any("仍有" in decision.reason for decision in report.plan.decisions)


def test_duplicate_direct_destination_blocks_all_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    path = next(node for node in tree.getroot().iter()
                if definition_ref(node, namespace) == defs.PDUR_PATH
                and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_SRC_MSG_100_SRC")
    group = path.find(f"{{{namespace}}}SUB-CONTAINERS")
    destination = next(node for node in group if definition_ref(node, namespace) == defs.PDUR_DEST)
    group.append(etree.fromstring(etree.tostring(destination)))
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "ambiguous_direct.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_DELETE_DESTINATION_AMBIGUOUS" in {issue.code for issue in report.errors}


def test_duplicate_direct_source_candidate_blocks_all_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    path = next(node for node in tree.getroot().iter()
                if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_SRC_MSG_100_SRC")
    group = path.find(f"{{{namespace}}}SUB-CONTAINERS")
    source = next(node for node in group if definition_ref(node, namespace) == defs.PDUR_SRC)
    duplicate = etree.fromstring(etree.tostring(source))
    duplicate.find(f"{{{namespace}}}SHORT-NAME").text = "DuplicateSource"
    group.append(duplicate)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "duplicate_source.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_DELETE_SOURCE_AMBIGUOUS" in {issue.code for issue in report.errors}


def test_direct_delete_preserves_path_with_unknown_manual_child(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    path = next(node for node in tree.getroot().iter()
                if definition_ref(node, namespace) == defs.PDUR_PATH
                and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_SRC_MSG_100_SRC")
    group = path.find(f"{{{namespace}}}SUB-CONTAINERS")
    destination = next(node for node in group if definition_ref(node, namespace) == defs.PDUR_DEST)
    manual = etree.fromstring(etree.tostring(destination))
    manual.find(f"{{{namespace}}}SHORT-NAME").text = "ManualChild"
    manual.find(f"{{{namespace}}}DEFINITION-REF").text = "/Manual/PduRChild"
    group.append(manual)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "manual_child_direct.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    path_name = "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC"
    assert len(index.find_by_path(path_name)) == 1
    assert not index.find_by_path(f"{path_name}/DST_MSG_200_DST")
    assert len(index.find_by_path(f"{path_name}/ManualChild")) == 1
    assert len(index.find_by_path(f"{path_name}/SRC_MSG_100_SRC")) == 1
    assert any(decision.category == "DIRECT_MANUAL_CHILD" for decision in report.plan.decisions)


def test_direct_destination_with_external_reference_blocks_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    destination = (
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/"
        "GWT_SRC_MSG_100_SRC/DST_MSG_200_DST"
    )
    _append_reference_to_shared_node(baseline, destination)
    output = tmp_path / "referenced_direct_destination.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert any(issue.code == "DIRECT_DELETE_DESTINATION_REFERENCED"
               for issue in report.all_issues)


def test_target_canif_with_manual_subcontainer_is_retained(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    target = next(node for node in tree.getroot().iter()
                  if definition_ref(node, namespace) == defs.CANIF_TX
                  and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_DST_MSG_DST_Tx")
    group = etree.SubElement(target, f"{{{namespace}}}SUB-CONTAINERS")
    manual = etree.SubElement(group, f"{{{namespace}}}ECUC-CONTAINER-VALUE")
    etree.SubElement(manual, f"{{{namespace}}}SHORT-NAME").text = "ManualChild"
    etree.SubElement(manual, f"{{{namespace}}}DEFINITION-REF").text = "/Manual/CanIfChild"
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "manual_child_canif.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    canif_path = "/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_DST_Tx"
    assert len(index.find_by_path(canif_path)) == 1
    assert len(index.find_by_path(f"{canif_path}/ManualChild")) == 1
    assert len(index.find_by_path("/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_DST_Tx")) == 1


def test_target_canif_shared_by_external_reference_is_retained(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    target = "/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_DST_Tx"
    _append_reference_to_shared_node(baseline, target)
    output = tmp_path / "shared_canif.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    assert len(index.find_by_path(target)) == 1
    assert len(index.find_by_path("/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_DST_Tx")) == 1
    assert any(decision.category == "DIRECT_SHARED_CANIF" for decision in report.plan.decisions)


def test_target_ecuc_shared_by_external_reference_is_retained_after_tx_cleanup(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    target = "/Cfg/EcuC/EcucPduCollection/GWT_DST_MSG_DST_Tx"
    _append_reference_to_shared_node(baseline, target)
    output = tmp_path / "shared_ecuc.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    index = ArxmlDocument.load(output).build_index()
    assert not index.find_by_path("/Cfg/CanIf/CanIfInitCfg/GWT_DST_MSG_DST_Tx")
    assert len(index.find_by_path(target)) == 1
    assert any(decision.category == "DIRECT_SHARED_ECUC" for decision in report.plan.decisions)


def test_source_chain_shared_by_other_pdur_path_is_retained(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    original = next(node for node in tree.getroot().iter()
                    if definition_ref(node, namespace) == defs.PDUR_PATH
                    and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_SRC_MSG_100_SRC")
    shared = etree.fromstring(etree.tostring(original))
    shared.find(f"{{{namespace}}}SHORT-NAME").text = "ManualSharedSourcePath"
    group = shared.find(f"{{{namespace}}}SUB-CONTAINERS")
    for child in list(group):
        if definition_ref(child, namespace) == defs.PDUR_DEST:
            group.remove(child)
    original.getparent().append(shared)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "shared_source.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success and not output.exists()
    assert "DIRECT_DELETE_SOURCE_AMBIGUOUS" in {issue.code for issue in report.errors}


def test_manual_or_incomplete_direct_object_is_not_guessed_or_deleted(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    target = next(node for node in tree.getroot().iter()
                  if definition_ref(node, namespace) == defs.CANIF_TX
                  and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_DST_MSG_DST_Tx")
    target.find(f"{{{namespace}}}SHORT-NAME").text = "ManualTx"
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "manual_blocked.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "DIRECT_DELETE_TARGET_SEMANTIC_CONFLICT" in {
        issue.code for issue in report.errors
    }


def test_delete_only_signal_destination_removes_mapping_and_matching_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    output = tmp_path / "signal_deleted.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_deleted_count == 1
    assert report.plan.signal_timeout_removed_count == 1
    document = ArxmlDocument.load(output)
    index = document.build_index()
    assert not index.find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")
    source = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert defs.COM_TIMEOUT not in parameters
    assert defs.COM_TIMEOUT_ACTION not in parameters


def test_delete_lin_destination_by_unique_message_signal_relationship(
    workbook_factory: object, lin_target_arxml_factory: object, tmp_path: Path,
) -> None:
    """LIN DELETE 不硬编码逻辑节点到物理通道的项目映射。"""
    dbc = lin_target_arxml_factory(channels=("LIN04",))
    add_config = workbook_factory(
        filename="lin_add_v4.84.xlsx", direct_rows=(),
        signal_rows=(signal_row(**{"目标网段": "PSMM"}),),
    )
    baseline = tmp_path / "lin_routed.arxml"
    assert generate_inputs(add_config, dbc, baseline).is_success

    delete_config = workbook_factory(
        filename="lin_delete_v4.84.xlsx", direct_rows=(),
        signal_rows=(_delete_signal(**{"目标网段": "PSMM"}),),
    )
    output = tmp_path / "lin_deleted.arxml"
    report = generate_inputs(delete_config, baseline, output)
    assert report.is_success, _messages(report)
    assert report.plan.signal_deleted_count == 1
    assert not ArxmlDocument.load(output).build_index().find_by_path(
        "/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC",
    )


def test_one_to_many_signal_delete_keeps_mapping_source_and_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    second = signal_row(**{"目标信号名": "DST_SIG_2"})
    baseline = generated_arxml_factory(signal_rows=(signal_row(), second))
    output = tmp_path / "signal_one_leg_deleted.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    index = document.build_index()
    mapping_path = "/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC"
    assert len(index.find_by_path(mapping_path)) == 1
    assert not index.find_by_path(f"{mapping_path}/ComGwDestination_DST_SIG_DST")
    assert len(index.find_by_path(f"{mapping_path}/ComGwDestination_DST_SIG_2_DST")) == 1
    source = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert parameters[defs.COM_TIMEOUT] == ("2",)
    assert report.plan.signal_timeout_retained_count == 1


def test_signal_delete_without_timeout_evidence_preserves_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    output = tmp_path / "timeout_retained.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(), signal_rows=(_delete_signal(**{"超时时间": None, "超时值": None}),),
        ), baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    source = document.build_index().find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert parameters[defs.COM_TIMEOUT] == ("2",)
    assert any("未提供超时时间" in decision.reason for decision in report.plan.decisions)


def test_signal_delete_mismatched_timeout_preserves_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    output = tmp_path / "timeout_mismatch.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(**{"超时时间": 4}),)),
        baseline, output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_timeout_retained_count == 1
    assert any("不一致" in decision.reason for decision in report.plan.decisions)


def test_signal_delete_with_substitution_evidence_clears_all_managed_timeout_parameters(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    row = signal_row(**{"超时值": "1.25"})
    baseline = generated_arxml_factory(signal_rows=(row,))
    output = tmp_path / "all_timeout_removed.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(**{"超时值": "1.25"}),)),
        baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    source = document.build_index().find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert defs.COM_TIMEOUT_ACTION not in parameters
    assert defs.COM_TIMEOUT not in parameters
    assert defs.COM_TIMEOUT_SUBSTITUTION not in parameters


def test_duplicate_timeout_parameter_is_conservatively_retained(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    source = next(node for node in tree.getroot().iter()
                  if node.findtext(f"{{{namespace}}}SHORT-NAME") == "SRC_SIG_oSRC_MSG_oSRC_Rx")
    group = source.find(f"{{{namespace}}}PARAMETER-VALUES")
    timeout = next(entry for entry in group if definition_ref(entry, namespace) == defs.COM_TIMEOUT)
    group.append(etree.fromstring(etree.tostring(timeout)))
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "duplicate_timeout_retained.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_timeout_retained_count == 1


def test_other_mapping_using_same_source_preserves_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    mapping = next(node for node in tree.getroot().iter()
                   if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_Sig_SRC_SIG_SRC")
    other = etree.fromstring(etree.tostring(mapping))
    other.find(f"{{{namespace}}}SHORT-NAME").text = "OtherSourceMapping"
    destination = next(node for node in other.iter()
                       if definition_ref(node, namespace) == defs.COM_GW_DEST)
    destination.find(f"{{{namespace}}}SHORT-NAME").text = "OtherDestination"
    reference = next(node for node in destination.iter()
                     if definition_ref(node, namespace) == defs.COM_GW_DEST_SIGNAL_REF)
    reference.find(f"{{{namespace}}}VALUE-REF").text = "/Cfg/Com/ComConfig/DST_SIG_2_oDST_MSG_oDST_Tx"
    mapping.getparent().append(other)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "other_mapping_timeout.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_timeout_retained_count == 1
    assert any("另外 1 个 Mapping" in decision.reason for decision in report.plan.decisions)


def test_deleting_all_mappings_for_same_source_uses_projected_state_for_timeout(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    mapping = next(node for node in tree.getroot().iter()
                   if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_Sig_SRC_SIG_SRC")
    other = etree.fromstring(etree.tostring(mapping))
    other.find(f"{{{namespace}}}SHORT-NAME").text = "OtherSourceMapping"
    destination = next(node for node in other.iter()
                       if definition_ref(node, namespace) == defs.COM_GW_DEST)
    destination.find(f"{{{namespace}}}SHORT-NAME").text = "OtherDestination"
    reference = next(node for node in destination.iter()
                     if definition_ref(node, namespace) == defs.COM_GW_DEST_SIGNAL_REF)
    reference.find(f"{{{namespace}}}VALUE-REF").text = \
        "/Cfg/Com/ComConfig/DST_SIG_2_oDST_MSG_oDST_Tx"
    mapping.getparent().append(other)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "all_source_mappings_deleted.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(),
            signal_rows=(
                _delete_signal(),
                _delete_signal(**{"目标信号名": "DST_SIG_2"}),
            ),
        ),
        baseline,
        output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_deleted_count == 2
    assert report.plan.signal_timeout_removed_count == 1
    assert report.plan.signal_timeout_retained_count == 0
    document = ArxmlDocument.load(output)
    index = document.build_index()
    assert not index.find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")
    assert not index.find_by_path("/Cfg/Com/ComConfig/OtherSourceMapping")
    source = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert defs.COM_TIMEOUT not in parameters


def test_duplicate_signal_destination_blocks_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    mapping = next(node for node in tree.getroot().iter()
                   if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_Sig_SRC_SIG_SRC")
    group = mapping.find(f"{{{namespace}}}SUB-CONTAINERS")
    destination = next(node for node in group if definition_ref(node, namespace) == defs.COM_GW_DEST)
    group.append(etree.fromstring(etree.tostring(destination)))
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "signal_ambiguous.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()


def test_duplicate_signal_source_container_blocks_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    mapping = next(node for node in tree.getroot().iter()
                   if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_Sig_SRC_SIG_SRC")
    group = mapping.find(f"{{{namespace}}}SUB-CONTAINERS")
    source = next(node for node in group if definition_ref(node, namespace) == defs.COM_GW_SOURCE)
    duplicate = etree.fromstring(etree.tostring(source))
    duplicate.find(f"{{{namespace}}}SHORT-NAME").text = "DuplicateSource"
    group.append(duplicate)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "duplicate_signal_source.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert any(issue.code == "SIGNAL_DELETE_SOURCE_AMBIGUOUS" for issue in report.all_issues)


def test_signal_delete_preserves_mapping_with_unknown_manual_child(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    mapping = next(node for node in tree.getroot().iter()
                   if node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_Sig_SRC_SIG_SRC")
    group = mapping.find(f"{{{namespace}}}SUB-CONTAINERS")
    destination = next(node for node in group if definition_ref(node, namespace) == defs.COM_GW_DEST)
    manual = etree.fromstring(etree.tostring(destination))
    manual.find(f"{{{namespace}}}SHORT-NAME").text = "ManualChild"
    manual.find(f"{{{namespace}}}DEFINITION-REF").text = "/Manual/ComGwChild"
    group.append(manual)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "manual_child_signal.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    index = document.build_index()
    mapping_path = "/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC"
    assert len(index.find_by_path(mapping_path)) == 1
    assert not index.find_by_path(f"{mapping_path}/ComGwDestination_DST_SIG_DST")
    assert len(index.find_by_path(f"{mapping_path}/ManualChild")) == 1
    assert len(index.find_by_path(f"{mapping_path}/ComGwSource_SRC_SIG_SRC")) == 1
    source = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert parameters[defs.COM_TIMEOUT] == ("2",)


def test_signal_destination_nested_object_with_external_reference_blocks_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    nested_destination = (
        "/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC/"
        "ComGwDestination_DST_SIG_DST/ComGwSignal"
    )
    _append_reference_to_shared_node(baseline, nested_destination)
    output = tmp_path / "referenced_nested_signal_destination.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert any(issue.code == "SIGNAL_DELETE_DESTINATION_REFERENCED"
               for issue in report.all_issues)


def test_signal_delete_is_idempotent_after_first_delete(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    first = tmp_path / "signal_first_delete.arxml"
    config = workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),))
    assert generate_inputs(config, baseline, first).is_success
    second = tmp_path / "signal_second_delete.arxml"
    report = generate_inputs(config, first, second)
    assert report.is_success, _messages(report)
    assert report.plan.signal_missing_count == 1


def test_same_direct_key_delete_add_replaces_parameters_on_projection(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    replacement = direct_row(**{"源网段报文Length": 12, "目标网段报文Length": 12})
    output = tmp_path / "direct_replaced.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(), replacement), signal_rows=()), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert (report.plan.direct_deleted_count, report.plan.direct_added_count) == (1, 1)
    document = ArxmlDocument.load(output)
    source = document.build_index().find_by_path(
        "/Cfg/EcuC/EcucPduCollection/GWT_SRC_MSG_SRC_Rx",
    )[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert parameters[defs.ECUC_PDU_LENGTH] == ("12",)


def test_missing_delete_then_add_same_key_still_creates_route(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "missing_then_add.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(), direct_row()), signal_rows=()),
        arxml_factory(), output,
    )
    assert report.is_success, _messages(report)
    assert (report.plan.direct_missing_count, report.plan.direct_added_count) == (1, 1)


def test_unsafe_delete_blocks_same_key_add_replacement(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    path = next(node for node in tree.getroot().iter()
                if definition_ref(node, namespace) == defs.PDUR_PATH
                and node.findtext(f"{{{namespace}}}SHORT-NAME") == "GWT_SRC_MSG_100_SRC")
    group = path.find(f"{{{namespace}}}SUB-CONTAINERS")
    destination = next(node for node in group if definition_ref(node, namespace) == defs.PDUR_DEST)
    group.append(etree.fromstring(etree.tostring(destination)))
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    before = _fingerprint(baseline)

    output = tmp_path / "unsafe_delete_replacement.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(
                _delete_direct(),
                direct_row(**{"源网段报文Length": 12, "目标网段报文Length": 12}),
            ),
            signal_rows=(),
        ),
        baseline,
        output,
    )
    assert not report.is_success
    assert not output.exists()
    assert report.plan.direct_added_count == 0
    assert any(issue.code == "DIRECT_DELETE_DESTINATION_AMBIGUOUS"
               for issue in report.all_issues)
    assert _fingerprint(baseline) == before


def test_signal_delete_and_add_same_source_preserves_timeout_for_new_route(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    replacement = signal_row(**{"目标信号名": "DST_SIG_2"})
    output = tmp_path / "signal_replaced.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(), replacement)), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert (report.plan.signal_deleted_count, report.plan.signal_added_count) == (1, 1)
    assert report.plan.signal_timeout_retained_count == 1
    document = ArxmlDocument.load(output)
    mapping = document.build_index().find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")[0]
    destinations = [node for node in mapping.iter()
                    if definition_ref(node, document.namespace) == defs.COM_GW_DEST]
    assert len(destinations) == 1
    _, references = semantic_values(destinations[0], document.namespace, recursive=True)
    assert references[defs.COM_GW_DEST_SIGNAL_REF] == (
        "/Cfg/Com/ComConfig/DST_SIG_2_oDST_MSG_oDST_Tx",
    )


def test_direct_and_signal_add_delete_execute_in_one_transaction(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(
        direct_rows=(direct_row(),), signal_rows=(signal_row(),),
    )
    direct_add = direct_row(**{"目标网段报文名称": "DST_NEW", "目标网段报文CANID": "0x211"})
    signal_add = signal_row(**{"目标信号名": "DST_SIG_2"})
    output = tmp_path / "mixed_transaction.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(_delete_direct(), direct_add),
            signal_rows=(_delete_signal(), signal_add),
        ), baseline, output,
    )
    assert report.is_success, _messages(report)
    assert (report.plan.direct_deleted_count, report.plan.direct_added_count) == (1, 1)
    assert (report.plan.signal_deleted_count, report.plan.signal_added_count) == (1, 1)


def test_add_failure_after_delete_leaves_no_output_and_baseline_unchanged(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    before = _fingerprint(baseline)
    output = tmp_path / "add_failed_after_delete.arxml"

    def fail_apply(*args: object, **kwargs: object) -> None:
        raise ArxmlStructureError("故障注入：ADD 应用失败")

    monkeypatch.setattr("davinci_gw.routing.transaction.AddCoordinator.apply", fail_apply)
    report = generate_inputs(
        workbook_factory(
            direct_rows=(
                _delete_direct(),
                direct_row(**{"目标网段报文名称": "DST_NEW", "目标网段报文CANID": "0x211"}),
            ), signal_rows=(),
        ), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert _fingerprint(baseline) == before


def test_parameter_delete_output_validation_failure_leaves_no_target_or_temp(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = generated_arxml_factory(signal_rows=(signal_row(),))
    before = _fingerprint(baseline)
    output = tmp_path / "parameter_validation_failed.arxml"

    def fail_validation(*args: object, **kwargs: object) -> None:
        raise OutputValidationError("故障注入：参数删除验证失败")

    monkeypatch.setattr(
        "davinci_gw.application.generate.validate_generated_output", fail_validation,
    )
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(_delete_signal(),)), baseline, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []
    assert _fingerprint(baseline) == before


def test_serialization_failure_after_delete_leaves_no_output_temp_or_baseline_change(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    before = _fingerprint(baseline)
    output = tmp_path / "serialization_failed_after_delete.arxml"

    def fail_serialization(*args: object, **kwargs: object) -> None:
        raise OSError(28, "故障注入：磁盘空间不足")

    monkeypatch.setattr("davinci_gw.arxml.document._serialize_tree", fail_serialization)
    report = generate_inputs(
        workbook_factory(direct_rows=(_delete_direct(),), signal_rows=()), baseline, output,
    )
    assert not report.is_success
    assert any(issue.code == "OUTPUT_WRITE_FAILED" for issue in report.all_issues)
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp.arxml")) == []
    assert _fingerprint(baseline) == before


def test_transaction_input_row_order_produces_identical_output(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    first_baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    second_baseline = tmp_path / "order_second_baseline.arxml"
    second_baseline.write_bytes(first_baseline.read_bytes())
    replacement = direct_row(**{"源网段报文Length": 12, "目标网段报文Length": 12})
    forward = workbook_factory(
        filename="transaction_forward_v4.84.xlsx",
        direct_rows=(_delete_direct(), replacement), signal_rows=(),
    )
    reverse = workbook_factory(
        filename="transaction_reverse_v4.84.xlsx",
        direct_rows=(replacement, _delete_direct()), signal_rows=(),
    )
    first_output = tmp_path / "transaction_forward.arxml"
    second_output = tmp_path / "transaction_reverse.arxml"
    assert generate_inputs(forward, first_baseline, first_output).is_success
    assert generate_inputs(reverse, second_baseline, second_output).is_success
    assert first_output.read_bytes() == second_output.read_bytes()


def test_repeating_same_replace_transaction_is_deterministic_and_nonduplicating(
    workbook_factory: object, generated_arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = generated_arxml_factory(direct_rows=(direct_row(),))
    replacement = direct_row(**{"源网段报文Length": 12, "目标网段报文Length": 12})
    config = workbook_factory(
        direct_rows=(_delete_direct(), replacement), signal_rows=(),
    )
    first = tmp_path / "replace_first.arxml"
    second = tmp_path / "replace_second.arxml"
    first_report = generate_inputs(config, baseline, first)
    second_report = generate_inputs(config, first, second)
    assert first_report.is_success and second_report.is_success
    assert first.read_bytes() == second.read_bytes()
