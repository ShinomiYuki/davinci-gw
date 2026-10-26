"""Com 端点依据方向和引用定位，生成名称只提供辅助身份。"""

from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import autosar_path
from davinci_gw.arxml.namespace import qualified
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.com_editor import ComEditor
from davinci_gw.modules.common import definition_ref
from tests.conftest import signal_row


def named(document, name):
    return document.build_index().find_by_short_name(name)[0]


def value(document, node, definition):
    entry = next(child for child in node.iter()
                 if definition_ref(child, document.namespace) == definition)
    return next(child for child in entry if etree.QName(child).localname in {"VALUE", "VALUE-REF"})


def rename(document, node, name):
    old = autosar_path(node, document.namespace)
    node.find(qualified(document.namespace, "SHORT-NAME")).text = name
    new = autosar_path(node, document.namespace)
    for reference in document.root.iter(qualified(document.namespace, "VALUE-REF")):
        if reference.text == old:
            reference.text = new


def locate(document, signal="SRC_SIG", network="SRC", direction="RECEIVE"):
    return ComEditor(document).locate_signal_endpoint("SRC_MSG", signal, network, direction)[0]


def save(document, path):
    etree.ElementTree(document.root).write(str(path), encoding="UTF-8", xml_declaration=True)


def test_arbitrary_generated_names_and_add_delete_share_identity(arxml_factory, workbook_factory, tmp_path):
    baseline = arxml_factory()
    document = ArxmlDocument.load(baseline)
    for old, new in (("SRC_MSG_oSRC_Rx", "InputEnvelope_Tx"),
                     ("DST_MSG_oDST_Tx", "OutputEnvelope_Rx"),
                     ("SRC_SIG_oSRC_MSG_oSRC_Rx", "TemperatureInput"),
                     ("DST_SIG_oDST_MSG_oDST_Tx", "TemperatureOutput"),
                     ("SRC_MSG_oSRC", "InputPdu"), ("DST_MSG_oDST", "OutputPdu")):
        rename(document, named(document, old), new)
    assert locate(document) == "FOUND"
    save(document, baseline)
    added = tmp_path / "added.arxml"
    report = generate_inputs(workbook_factory(direct_rows=()), baseline, added)
    assert report.is_success, [issue.message for issue in report.all_issues]
    assert report.plan.signal_added_count == 1
    deleted = generate_inputs(workbook_factory(filename="delete_v4.84.xlsx", direct_rows=(), signal_rows=(
        signal_row(**{"操作类型": "DELETE"}),
    )), added, tmp_path / "deleted.arxml")
    assert deleted.is_success, [issue.message for issue in deleted.all_issues]
    assert deleted.plan.signal_deleted_count == 1


@pytest.mark.parametrize("identity,state", [
    ("SRC_SIG", "FOUND"), ("SRC_SIG_SRCCAN_0520", "FOUND"),
    ("SRC_SIGReq", "SIGNAL_MISSING"), ("SRC_SIG_Backup", "IDENTITY_UNCONFIRMED"),
    ("SRC_SIG_OTHERCAN_0520", "IDENTITY_UNCONFIRMED"),
])
def test_only_confirmed_signal_suffix_is_an_alias(arxml_factory, identity, state):
    document = ArxmlDocument.load(arxml_factory())
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    rename(document, signal, f"{identity}_oSRC_MSG_oAnything")
    value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = f"/PDU/SRC_MSG_oSRC/{identity}"
    assert locate(document) == state


@pytest.mark.parametrize("direction,state", [("RECEIVE", "FOUND"), ("SEND", "IPDU_MISSING"),
                                              ("TRANSMIT", "REFERENCE_CONFLICT")])
def test_direction_parameter_overrides_name(arxml_factory, direction, state):
    document = ArxmlDocument.load(arxml_factory())
    ipdu = named(document, "SRC_MSG_oSRC_Rx")
    value(document, ipdu, defs.COM_IPDU_DIRECTION).text = direction
    assert locate(document) == state


def test_valid_exact_and_suffix_candidates_are_ambiguous(arxml_factory):
    document = ArxmlDocument.load(arxml_factory())
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    clone = deepcopy(signal)
    signal.getparent().append(clone)
    clone.find(qualified(document.namespace, "SHORT-NAME")).text = "OtherTemperature"
    value(document, clone, defs.COM_SYSTEM_SIGNAL_REF).text = "/PDU/SRC_MSG_oSRC/SRC_SIG_SRCCAN_0520"
    ipdu = named(document, "SRC_MSG_oSRC_Rx")
    reference = value(document, ipdu, defs.COM_IPDU_SIGNAL_REF).getparent()
    duplicate = deepcopy(reference)
    duplicate.find(qualified(document.namespace, "VALUE-REF")).text = autosar_path(clone, document.namespace)
    reference.getparent().append(duplicate)
    assert locate(document) == "SIGNAL_AMBIGUOUS"


@pytest.mark.parametrize("problem,state", [
    ("pdu_missing", "REFERENCE_CONFLICT"), ("pdu_duplicate", "REFERENCE_CONFLICT"),
    ("group_conflict", "REFERENCE_CONFLICT"), ("message_conflict", "REFERENCE_CONFLICT"),
    ("signal_reference", "REFERENCE_CONFLICT"), ("network_unknown", "OWNERSHIP_UNCONFIRMED"),
    ("ipdu_duplicate", "IPDU_AMBIGUOUS"),
    ("malformed_original", "REFERENCE_CONFLICT"),
])
def test_incomplete_or_conflicting_structure_is_not_selected(arxml_factory, problem, state):
    document = ArxmlDocument.load(arxml_factory())
    ipdu = named(document, "SRC_MSG_oSRC_Rx")
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    if problem == "pdu_missing":
        value(document, ipdu, defs.COM_PDU_ID_REF).text = "/Cfg/Unknown"
    elif problem == "pdu_duplicate":
        pdu = named(document, "SRC_MSG_oSRC")
        pdu.getparent().append(deepcopy(pdu))
    elif problem == "group_conflict":
        value(document, ipdu, defs.COM_IPDU_GROUP_REF).text = "/Cfg/Com/ComConfig/Group_oDST"
    elif problem == "message_conflict":
        value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = "/PDU/OTHER_MSG_oSRC/SRC_SIG"
    elif problem == "signal_reference":
        value(document, ipdu, defs.COM_IPDU_SIGNAL_REF).text = "/Cfg/MissingSignal"
    elif problem == "network_unknown":
        rename(document, named(document, "Group_oSRC"), "UnknownGroup")
        value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = "/PDU/SRC_MSG/SRC_SIG"
    elif problem == "malformed_original":
        value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = "InvalidReference"
    else:
        clone = deepcopy(ipdu)
        clone.find(qualified(document.namespace, "SHORT-NAME")).text = "AnotherEnvelope"
        ipdu.getparent().append(clone)
    assert locate(document) == state


def test_same_signal_in_other_message_is_not_selected(arxml_factory):
    document = ArxmlDocument.load(arxml_factory())
    pdu = named(document, "SRC_MSG_oSRC")
    rename(document, pdu, "OTHER_MSG_oSRC")
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = "/PDU/OTHER_MSG_oSRC/SRC_SIG"
    assert locate(document) == "IPDU_MISSING"


def test_missing_source_skips_all_legs(arxml_factory, workbook_factory, tmp_path):
    report = generate_inputs(workbook_factory(direct_rows=(), signal_rows=(
        signal_row(**{"源信号名": "ABSENT"}),
        signal_row(**{"源信号名": "ABSENT", "目标信号名": "DST_SIG_2"}),
    )), arxml_factory(), tmp_path / "skipped.arxml")
    assert report.is_success
    assert report.plan.signal_skipped_count == 2
    assert not report.plan.operations


def test_delete_blocks_opaque_dangling_reference(arxml_factory, workbook_factory, tmp_path):
    baseline = arxml_factory()
    document = ArxmlDocument.load(baseline)
    ipdu = named(document, "SRC_MSG_oSRC_Rx")
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    rename(document, signal, "OpaqueInput")
    save(document, baseline)
    added = tmp_path / "added.arxml"
    assert generate_inputs(workbook_factory(direct_rows=()), baseline, added).is_success
    document = ArxmlDocument.load(added)
    for name in ("SRC_MSG_oSRC_Rx", "OpaqueInput"):
        node = named(document, name)
        node.getparent().remove(node)
    save(document, added)
    report = generate_inputs(workbook_factory(filename="delete_v4.84.xlsx", direct_rows=(), signal_rows=(
        signal_row(**{"操作类型": "DELETE"}),
    )), added, tmp_path / "blocked.arxml")
    assert not report.is_success
    assert any(issue.code == "SIGNAL_DELETE_DANGLING_REFERENCE" for issue in report.all_issues)


@pytest.mark.parametrize("operation", ["ADD", "DELETE"])
def test_lin_fallback_rejects_conflicting_message_ownership(
    lin_target_arxml_factory, workbook_factory, tmp_path, operation,
):
    baseline = lin_target_arxml_factory()
    document = ArxmlDocument.load(baseline)
    signal = named(document, "DST_SIG_oDST_MSG_oLIN04_Tx")
    value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = "/PDU/OTHER_MSG_oLIN04/DST_SIG"
    save(document, baseline)
    report = generate_inputs(workbook_factory(direct_rows=(), signal_rows=(
        signal_row(**{"目标网段": "PSMM", "操作类型": operation}),
    )), baseline, tmp_path / "blocked.arxml")
    assert not report.is_success
    assert any("冲突" in issue.message for issue in report.all_issues)


@pytest.mark.parametrize("identity", ["SRC_SIG", "SRC_SIG_SRCCAN_0520"])
def test_delete_guard_recognizes_bare_message_in_original_reference(
    arxml_factory, workbook_factory, tmp_path, identity,
):
    baseline = arxml_factory()
    document = ArxmlDocument.load(baseline)
    signal = named(document, "SRC_SIG_oSRC_MSG_oSRC_Rx")
    rename(document, signal, "OpaqueInput")
    value(document, signal, defs.COM_SYSTEM_SIGNAL_REF).text = f"/PDU/SRC_MSG/{identity}"
    save(document, baseline)
    added = tmp_path / "added.arxml"
    assert generate_inputs(workbook_factory(direct_rows=()), baseline, added).is_success
    document = ArxmlDocument.load(added)
    ipdu = named(document, "SRC_MSG_oSRC_Rx")
    ipdu.getparent().remove(ipdu)
    save(document, added)
    report = generate_inputs(workbook_factory(filename="delete_v4.84.xlsx", direct_rows=(), signal_rows=(
        signal_row(**{"操作类型": "DELETE"}),
    )), added, tmp_path / "blocked.arxml")
    assert not report.is_success
    assert any(issue.code == "SIGNAL_DELETE_DANGLING_REFERENCE" for issue in report.all_issues)
