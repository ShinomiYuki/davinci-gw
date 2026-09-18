"""第02轮直接报文 ADD、幂等、冲突与跳过语义测试。"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, duplicate_uuids, semantic_values
from tests.conftest import direct_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _handle_values(document: ArxmlDocument, definition: str) -> list[str]:
    values: list[str] = []
    for node in document.root.iter():
        if definition_ref(node, document.namespace) != definition:
            continue
        for child in node:
            if isinstance(child.tag, str) and etree.QName(child).localname == "VALUE":
                values.append(child.text or "")
    return values


def test_single_direct_route_adds_complete_chain(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    baseline = arxml_factory()
    output = tmp_path / "generated.arxml"
    report = generate_inputs(config, baseline, output)
    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 1
    document = ArxmlDocument.load(output)
    index = document.build_index()
    expected = (
        "/Cfg/EcuC/EcucPduCollection/GWT_EcuC_SRC_MSG_100_SRC_CAN_Rx",
        "/Cfg/EcuC/EcucPduCollection/GWT_EcuC_DST_MSG_200_DST_CAN_Tx",
        "/Cfg/CanIf/CanIfInitCfg/GWT_CanIf_SRC_MSG_100_SRC_CAN_Rx",
        "/Cfg/CanIf/CanIfInitCfg/GWT_CanIf_DST_MSG_200_DST_CAN_Tx",
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC",
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC/SRC_MSG_100_SRC",
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC/DST_MSG_200_DST",
    )
    assert all(len(index.find_by_path(path)) == 1 for path in expected)
    assert all(len(index.find_by_path(target)) == 1 for target in report.plan.expected_internal_references)


def test_one_source_multiple_destinations_reuses_source(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory(
        direct_rows=(direct_row(), direct_row(**{
            "目标网段报文名称": "DST_MSG_2", "目标网段报文CANID": "0x201",
        })), signal_rows=(),
    )
    output = tmp_path / "one_to_many.arxml"
    report = generate_inputs(config, arxml_factory(), output)
    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 2
    path = ArxmlDocument.load(output).build_index().find_by_path(
        "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/GWT_SRC_MSG_100_SRC",
    )[0]
    subcontainers = path.find(f"{{{etree.QName(path).namespace}}}SUB-CONTAINERS")
    definitions = [definition_ref(child, etree.QName(path).namespace) for child in subcontainers]
    assert definitions.count(defs.PDUR_SRC) == 1
    assert definitions.count(defs.PDUR_DEST) == 2


def test_second_run_is_idempotent(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    first = tmp_path / "first.arxml"
    second = tmp_path / "second.arxml"
    assert generate_inputs(config, arxml_factory(), first).is_success
    report = generate_inputs(config, first, second)
    assert report.is_success, _messages(report)
    assert report.plan.direct_added_count == 0
    assert report.plan.direct_existing_count == 1


def test_missing_hrh_skips_bad_group_but_adds_good_group(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    refs = (
        {"CAN通道名称": "BAD_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "NO_HRH"},
        {"CAN通道名称": "SRC_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH"},
        {"CAN通道名称": "DST_CAN", "CanIfTxBuffer名称": "TX2", "CanIfHrh名称": "HRH2"},
    )
    rows = (
        direct_row(**{"源网段报文名称": "BAD_MSG", "源网段CAN通道": "BAD_CAN"}),
        direct_row(),
    )
    report = generate_inputs(
        workbook_factory(direct_rows=rows, signal_rows=(), reference_rows=refs),
        arxml_factory(), tmp_path / "partial.arxml",
    )
    assert report.is_success, _messages(report)
    assert (report.plan.direct_added_count, report.plan.direct_skipped_count) == (1, 1)
    assert "NO_HRH" in _messages(report) and "已跳过" in _messages(report)


def test_missing_tx_buffer_skips_only_target_leg(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    refs = (
        {"CAN通道名称": "SRC_CAN", "CanIfTxBuffer名称": "TX", "CanIfHrh名称": "HRH"},
        {"CAN通道名称": "DST_CAN", "CanIfTxBuffer名称": "NO_TX", "CanIfHrh名称": "HRH2"},
    )
    report = generate_inputs(
        workbook_factory(signal_rows=(), reference_rows=refs), arxml_factory(),
        tmp_path / "skipped.arxml",
    )
    assert report.is_success
    assert report.plan.direct_added_count == 0
    assert report.plan.direct_skipped_count == 1
    assert "NO_TX" in _messages(report)


def test_same_name_different_semantics_blocks_output(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory(signal_rows=())
    first = tmp_path / "first.arxml"
    assert generate_inputs(config, arxml_factory(), first).is_success
    tree = etree.parse(str(first))
    namespace = etree.QName(tree.getroot()).namespace
    for node in tree.getroot().iter(f"{{{namespace}}}ECUC-CONTAINER-VALUE"):
        short = node.find(f"{{{namespace}}}SHORT-NAME")
        if short is not None and short.text == "GWT_EcuC_DST_MSG_200_DST_CAN_Tx" and definition_ref(node, namespace) == defs.ECUC_PDU:
            parameters, _ = semantic_values(node, namespace)
            value = parameters[defs.ECUC_PDU_LENGTH][0]
            assert value == "8"
            for entry in node.iter():
                if definition_ref(entry, namespace) == defs.ECUC_PDU_LENGTH:
                    entry.find(f"{{{namespace}}}VALUE").text = "9"
                    break
            break
    tree.write(str(first), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "blocked.arxml"
    report = generate_inputs(config, first, output)
    assert not report.is_success
    assert not output.exists()
    assert "CONFLICT" in " ".join(issue.code for issue in report.errors)


def test_generated_uuid_and_handles_are_unique(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "unique.arxml"
    report = generate_inputs(workbook_factory(signal_rows=()), arxml_factory(), output)
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    assert duplicate_uuids(document.root) == ()
    for definition in (defs.CANIF_RX_HANDLE, defs.CANIF_TX_HANDLE, defs.PDUR_SRC_HANDLE, defs.PDUR_DEST_HANDLE):
        values = _handle_values(document, definition)
        assert len(values) == len(set(values))


def test_legacy_duplicate_uuid_does_not_block_unique_new_objects(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    baseline = arxml_factory(filename="legacy_duplicate_uuid.arxml")
    tree = etree.parse(str(baseline))
    nodes = [node for node in tree.getroot().iter() if node.get("UUID")]
    assert len(nodes) >= 2
    legacy_uuid = nodes[0].get("UUID")
    nodes[1].set("UUID", legacy_uuid)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    output = tmp_path / "legacy_duplicate_uuid_output.arxml"
    report = generate_inputs(workbook_factory(signal_rows=()), baseline, output)
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    assert duplicate_uuids(document.root) == (legacy_uuid,)
    for operation in report.plan.operations:
        if operation.short_name:
            node = document.build_index().find_by_path(operation.object_path)[0]
            assert node.get("UUID") != legacy_uuid


def test_route_row_order_produces_identical_arxml(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    first_route = direct_row(**{
        "目标网段报文名称": "DST_A", "目标网段报文CANID": "0x201",
    })
    second_route = direct_row(**{
        "目标网段报文名称": "DST_Z", "目标网段报文CANID": "0x202",
    })
    forward = workbook_factory(
        filename="forward_v4.84.xlsx", direct_rows=(first_route, second_route), signal_rows=(),
    )
    reverse = workbook_factory(
        filename="reverse_v4.84.xlsx", direct_rows=(second_route, first_route), signal_rows=(),
    )
    first_output = tmp_path / "forward.arxml"
    second_output = tmp_path / "reverse.arxml"
    assert generate_inputs(forward, arxml_factory(filename="forward_base.arxml"), first_output).is_success
    assert generate_inputs(reverse, arxml_factory(filename="reverse_base.arxml"), second_output).is_success
    assert first_output.read_bytes() == second_output.read_bytes()
