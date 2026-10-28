"""清理 skill 的引用识别、精确删除和文件保护测试。"""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import semantic_values
from tests.conftest import direct_row
from tests.unit.test_direct_truncation import TX_PATH, set_truncation
from tests.unit.test_diagnostic_routes import diagnostic_case
from tests.unit.test_doip_routes import doip_case, functional_sample

SCRIPT = Path(__file__).resolve().parents[2] / "skills/remove-routed-tx-truncation/scripts/remove_routed_tx_truncation.py"
SPEC = importlib.util.spec_from_file_location("remove_routed_tx_truncation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
clean = MODULE.clean


@pytest.fixture
def sample(workbook_factory, arxml_factory, tmp_path):
    path = tmp_path / "sample.arxml"
    assert generate_inputs(workbook_factory(signal_rows=()), arxml_factory(), path).is_success
    set_truncation(path, "true")
    return path


def edit(path, callback):
    document = ArxmlDocument.load(path)
    callback(document, document.build_index())
    document.tree.write(str(path), encoding="UTF-8", xml_declaration=True)


def add_reference(document, owner, definition, target):
    ns = document.namespace
    group = owner.find(f"{{{ns}}}REFERENCE-VALUES")
    if group is None:
        group = etree.SubElement(owner, f"{{{ns}}}REFERENCE-VALUES")
    entry = etree.SubElement(group, f"{{{ns}}}ECUC-REFERENCE-VALUE")
    etree.SubElement(entry, f"{{{ns}}}DEFINITION-REF").text = definition
    etree.SubElement(entry, f"{{{ns}}}VALUE-REF").text = target


@pytest.mark.parametrize("value", ["true", "false"])
def test_exact_removal_preview_cli_and_idempotence(sample, tmp_path, value):
    set_truncation(sample, value)
    # 历史对象可无 GWT 前缀，报文名含 Diag 也不表示 CanTp 业务。
    def rename(document, index):
        for node in document.root.iter():
            if node.text:
                node.text = node.text.replace("GWT_", "Legacy_").replace("DST_MSG", "EPB_RemoteDiagL")
    edit(sample, rename)
    original = sample.read_bytes()
    environment = dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING="cp936")
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(sample)],
                            capture_output=True, text=True, encoding="utf-8", env=environment)
    assert result.returncode == 0, result.stderr
    preview = json.loads(result.stdout)
    assert preview["deleted_parameter_count"] == 1
    assert preview["changed"][0]["path"].endswith("Legacy_CanIf_EPB_RemoteDiagL_200_DST_CAN_Tx")
    assert sample.read_bytes() == original

    output = tmp_path / "cleaned.arxml"
    report = clean(sample, output)
    assert report["deleted_parameter_count"] == 1
    assert sample.read_bytes() == original
    before, after = ArxmlDocument.load(sample), ArxmlDocument.load(output)
    tx_path = report["changed"][0]["path"]
    before_tx = before.build_index().find_by_path(tx_path)[0]
    after_tx = after.build_index().find_by_path(tx_path)[0]
    params, refs = semantic_values(before_tx, before.namespace)
    params.pop(d.CANIF_TX_TRUNCATION)
    assert semantic_values(after_tx, after.namespace) == (params, refs)
    # 移除预期节点后，完整树应保持原语义（含 Rx、其他 Tx、注释和引用）。
    entry = next(e for e in before_tx.find(f"{{{before.namespace}}}PARAMETER-VALUES")
                 if e.findtext(f"{{{before.namespace}}}DEFINITION-REF") == d.CANIF_TX_TRUNCATION)
    entry.getparent().remove(entry)
    assert etree.tostring(before.tree) == etree.tostring(after.tree)
    second = clean(output, tmp_path / "again.arxml")
    assert second["deleted_parameter_count"] == 0
    assert len(second["already_absent"]) == 1
    assert second["skipped"]  # ExistingTx 并非普通直接路由目标。


@pytest.mark.parametrize("mode", ["com", "cantp", "doip", "duplicate_tx", "duplicate_rx", "duplicate_path", "tp_route", "diag_tx", "missing_source"])
def test_conflicting_or_non_direct_business_is_preserved(sample, tmp_path, mode):
    def change(document, index):
        tx = index.find_by_path(TX_PATH)[0]
        ns = document.namespace
        pdu = semantic_values(tx, ns)[1][d.CANIF_TX_PDU_REF][0]
        route = index.find_by_short_name("GWT_SRC_MSG_100_SRC")[0]
        if mode in {"com", "cantp", "doip"}:
            owner = index.find_by_short_name("ExistingTx")[0]
            ref = {"com": d.COM_PDU_ID_REF, "cantp": f"{d.CANTP_TX_NPDU}/CanTpTxNPduRef",
                   "doip": "/MICROSAR/DoIP/DoIPConfig/DoIPTxPdu/DoIPTxPduRef"}[mode]
            add_reference(document, owner, ref, pdu)
        elif mode in {"duplicate_tx", "duplicate_rx", "duplicate_path"}:
            node = tx if mode == "duplicate_tx" else route if mode == "duplicate_path" else index.find_by_short_name("GWT_CanIf_SRC_MSG_100_SRC_CAN_Rx")[0]
            node.getparent().append(deepcopy(node))
        elif mode == "missing_source":
            source = next(node for node in route.find(f"{{{ns}}}SUB-CONTAINERS")
                          if node.findtext(f"{{{ns}}}DEFINITION-REF") == d.PDUR_SRC)
            source.getparent().remove(source)
        else:
            definition = d.PDUR_PATH_COMM_TYPE if mode == "tp_route" else d.CANIF_TX_CONFIRM_UL
            node = route if mode == "tp_route" else tx
            for entry in node.find(f"{{{ns}}}PARAMETER-VALUES"):
                if entry.findtext(f"{{{ns}}}DEFINITION-REF") == definition:
                    entry.find(f"{{{ns}}}VALUE").text = "TRANSPORT_PROTOCOL" if mode == "tp_route" else "CAN_TP"
    edit(sample, change)
    output = tmp_path / "skipped.arxml"
    report = clean(sample, output)
    assert report["deleted_parameter_count"] == 0
    assert any(item["path"] == TX_PATH and item["reason"] for item in report["skipped"])
    document = ArxmlDocument.load(output)
    assert semantic_values(document.build_index().find_by_path(TX_PATH)[0], document.namespace)[0][d.CANIF_TX_TRUNCATION] == ("true",)


def test_shared_ordinary_direct_target_is_cleaned_once(sample, workbook_factory, tmp_path):
    config = workbook_factory(filename="second_v4.84.xlsx", signal_rows=(), direct_rows=(direct_row(**{
        "源网段报文名称": "SRC_B", "源网段报文CANID": "0x101",
    }),))
    shared = tmp_path / "shared.arxml"
    report = generate_inputs(config, sample, shared)
    assert report.is_success, report.all_issues
    result = clean(shared, tmp_path / "shared_cleaned.arxml")
    assert result["deleted_parameter_count"] == 1
    assert len(result["changed"][0]["routes"]) == 2


def test_never_overwrites_input_or_existing_output(sample, tmp_path):
    original = sample.read_bytes()
    with pytest.raises(ValueError, match="禁止覆盖"):
        clean(sample, sample)
    output = tmp_path / "exists.arxml"
    output.write_text("已有文件", encoding="utf-8")
    with pytest.raises(ValueError, match="禁止覆盖"):
        clean(sample, output)
    assert sample.read_bytes() == original
    assert output.read_text(encoding="utf-8") == "已有文件"


@pytest.mark.parametrize("routed", [True, False])
def test_cantp_requires_actual_pdur_routing_and_preserves_unrouted_tx(diagnostic_case, tmp_path, routed):
    document, _, _, _ = diagnostic_case
    if not routed:
        for route in tuple(document.build_index().find_by_definition_ref(d.PDUR_PATH)):
            if semantic_values(route, document.namespace)[0].get(d.PDUR_PATH_COMM_TYPE) == ("TRANSPORT_PROTOCOL",):
                route.getparent().remove(route)
    source, output = tmp_path / "can_diag.arxml", tmp_path / "can_diag_clean.arxml"
    document.tree.write(str(source), encoding="UTF-8", xml_declaration=True)
    original = source.read_bytes()
    report = clean(source, output)
    assert report["deleted_parameter_count"] == (2 if routed else 0), report
    result = ArxmlDocument.load(output)
    for name in ("FrameSRC_CANTx", "FrameDST_CANTx"):
        parameters = semantic_values(result.build_index().find_by_short_name(name)[0], result.namespace)[0]
        assert (d.CANIF_TX_TRUNCATION not in parameters) == routed
    existing = result.build_index().find_by_short_name("ExistingTx")[0]
    assert semantic_values(existing, result.namespace)[0][d.CANIF_TX_TRUNCATION] == ("true",)
    assert source.read_bytes() == original
    again = clean(output, tmp_path / "can_diag_again.arxml")
    assert again["deleted_parameter_count"] == 0


def test_cantp_shared_with_local_business_is_preserved(diagnostic_case, tmp_path):
    document, _, _, _ = diagnostic_case
    index, ns = document.build_index(), document.namespace
    tx = index.find_by_short_name("FrameSRC_CANTx")[0]
    lower = semantic_values(tx, ns)[1][d.CANIF_TX_PDU_REF][0]
    add_reference(document, index.find_by_short_name("ExistingTx")[0], d.COM_PDU_ID_REF, lower)
    source = tmp_path / "shared_local.arxml"
    document.tree.write(str(source), encoding="UTF-8", xml_declaration=True)
    report = clean(source, tmp_path / "shared_local_clean.arxml")
    assert report["deleted_parameter_count"] == 1, report
    assert any(item["path"].endswith("FrameSRC_CANTx") and "共享" in item["reason"] for item in report["skipped"])


def test_different_complete_gateway_routes_can_share_a_queue(doip_case, tmp_path):
    document, _, _, _ = doip_case
    source = tmp_path / "shared_queue.arxml"
    document.tree.write(str(source), encoding="UTF-8", xml_declaration=True)
    report = clean(source, tmp_path / "shared_queue_clean.arxml")
    assert report["deleted_parameter_count"] == 2, report
    assert {item["path"].rsplit("/", 1)[-1] for item in report["changed"]} == {"FrameSRC_CANTx", "FrameDST_CANTx"}


def test_independent_local_api_destination_does_not_block_cantp_target(doip_case, tmp_path):
    document, _ = functional_sample(doip_case)
    index, ns = document.build_index(), document.namespace
    # 移除只有目标腿的模板路径，保留完整的功能请求及其本地转发目标。
    template = index.find_by_short_name("DST_CANFuncCanPath")[0]
    template.getparent().remove(template)
    local_before = semantic_values(index.find_by_short_name("LocalDcm")[0], ns)
    source, output = tmp_path / "local_fanout.arxml", tmp_path / "local_fanout_clean.arxml"
    document.tree.write(str(source), encoding="UTF-8", xml_declaration=True)
    report = clean(source, output)
    assert any(item["path"].endswith("DST_CANFuncFrame") for item in report["changed"]), report
    result = ArxmlDocument.load(output)
    index = result.build_index()
    assert semantic_values(index.find_by_short_name("LocalDcm")[0], ns) == local_before
    # 只有未配置源端的模板路径，不能构成 SRC_CAN 的真实路由证据。
    assert semantic_values(index.find_by_short_name("SRC_CANFuncFrame")[0], ns)[0][d.CANIF_TX_TRUNCATION] == ("true",)


@pytest.mark.parametrize("missing", ["frame", "npdu", "pdu"])
def test_incomplete_cantp_receive_chain_is_preserved(diagnostic_case, tmp_path, missing):
    document, _, _, _ = diagnostic_case
    index = document.build_index()
    if missing == "frame":
        node = index.find_by_short_name("FrameSRC_CANRx")[0]
    elif missing == "pdu":
        node = index.find_by_short_name("LowSRC_CANRx")[0]
    else:
        node = next(item for item in index.find_by_short_name("MisleadingNameTrue")[0].iter()
                    if item.findtext(f"{{{document.namespace}}}DEFINITION-REF") == d.CANTP_RX_NPDU)
    node.getparent().remove(node)
    source = tmp_path / "incomplete.arxml"
    document.tree.write(str(source), encoding="UTF-8", xml_declaration=True)
    report = clean(source, tmp_path / "incomplete_clean.arxml")
    assert report["deleted_parameter_count"] == 0, report
    assert any("CanTp" in item["reason"] or "引用" in item["reason"] for item in report["skipped"])
