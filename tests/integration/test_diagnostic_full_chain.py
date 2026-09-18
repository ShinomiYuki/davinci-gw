"""真实诊断需求先移除完整端点，再验证四模块从缺失状态生成。"""

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.modules import definitions as d
from davinci_gw.modules.common import definition_ref
from davinci_gw.routing.add import AddCoordinator
from davinci_gw.routing.diagnostic_route import DiagnosticRoutePlanner
from davinci_gw.routing.transaction import TransactionCoordinator
from davinci_gw.validation.output import validate_generated_output


@pytest.mark.slow
def test_real_diagnostic_full_chain_rebuild(tmp_path):
    config = os.environ.get("DAVINCI_GW_DIAGNOSTIC_CONFIG")
    if not config:
        pytest.skip("设置 DAVINCI_GW_DIAGNOSTIC_CONFIG 后执行真实诊断全链路验收")
    baseline = Path(__file__).resolve().parents[2] / "input" / "T13J_918.arxml"
    output_dir = Path(os.environ.get("DAVINCI_GW_DIAGNOSTIC_TEST_DIR", tmp_path))
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture = output_dir / "T13J_918_完整链路已移除.arxml"
    output = output_dir / "T13J_918_完整链路新增验证.arxml"
    assert not fixture.exists() and not output.exists(), "请指定新的验收输出目录"
    workbook = read_workbook(config)
    assert workbook.is_valid, workbook.issues
    document = ArxmlDocument.load(baseline)
    coordinator = AddCoordinator(document, workbook.data)
    planner = DiagnosticRoutePlanner(workbook.data, coordinator.ecuc, coordinator.canif,
                                     coordinator.pdur, coordinator.routing_groups)
    removed = set()
    channels = set()
    for route in workbook.data.diagnostic_routes:
        for endpoint, side in ((route.request_endpoint, True), (route.response_endpoint, False)):
            chain = planner.locate(endpoint, side)
            assert chain
            removed.update(chain.object_paths)
            channels.add(chain.channel_path)
    # 仅在测试副本解除这些端点的 PduR 使用方，避免构造悬空引用。
    # 外部模块本身不删除；从同一路径中移除引用被删端点的目标腿。
    for path in tuple(removed):
        for ref in planner.index.find_referrers(path):
            owner = planner.path(ref)
            if any(owner == p or owner.startswith(p + "/") for p in removed):
                continue
            node = planner.node(owner)
            definition = definition_ref(node, planner.ns)
            assert definition in {d.PDUR_SRC, d.PDUR_DEST}, (path, owner, definition)
            removed.add(planner.path(node.getparent().getparent()) if definition == d.PDUR_SRC else owner)
    for path in channels:
        sdus = planner.children(planner.node(path), d.CANTP_RX) + planner.children(planner.node(path), d.CANTP_TX)
        if all(planner.path(sdu) in removed for sdu in sdus):
            removed.add(path)
    for node in planner.index.find_by_definition_ref(d.PDUR_ROUTING_GROUP_DEST_REF):
        target = node.findtext(f"{{{planner.ns}}}VALUE-REF")
        if any(target == p or target.startswith(p + "/") for p in removed):
            node.getparent().remove(node)
    removal_counts = Counter()
    for path in sorted(removed, key=lambda value: value.count("/"), reverse=True):
        node = planner.node(path)
        removal_counts[definition_ref(node, planner.ns).rsplit("/", 1)[-1]] += 1
        node.getparent().remove(node)
    index = document.build_index()
    for path in removed:
        assert not index.find_by_path(path)
        assert not index.find_referrers(path), path
    document.root.getroottree().write(str(fixture), encoding="UTF-8", xml_declaration=True)
    report = generate_inputs(config, fixture, output)
    assert report.is_success, [issue.message for issue in report.all_issues]
    counts = Counter(op.definition_ref.rsplit("/", 1)[-1] for op in report.plan.operations if op.action.value == "CREATE")
    assert counts["CanIfRxPduCfg"] == 3 and counts["CanIfTxPduCfg"] == 3, counts
    assert counts["Pdu"] == 12, counts
    assert counts["CanTpChannel"] == 4, counts
    assert counts["CanTpRxNSdu"] == 3 and counts["CanTpTxNSdu"] == 3, counts
    assert counts["PduRDestPdu"] == 3 and counts["PduRQueue"] == 3, counts
    result = ArxmlDocument.load(output)
    validate_generated_output(result, report.plan)
    names = {op.short_name for op in report.plan.operations}
    assert {"Diag_TP_7E0_PTCAN", "Diag_TP_7E8_DGCAN", "Diag_TP_7DF_ICCAN"} <= names
    assert {"GWT_CanTpChannelGW_DGCAN7E0_7E8", "GWT_CanTpChannelGW_PTCAN7E0_7E8",
            "GWT_CanTpChannelGW_DGCAN7DF", "GWT_CanTpChannelGW_ICCAN7DF"} <= names
    repeated = TransactionCoordinator(result, workbook.data).plan_and_apply()
    assert not repeated.errors, [issue.message for issue in repeated.issues]
    assert not repeated.operations
    evidence = {"removed": dict(removal_counts), "created": dict(counts), "idempotent": True,
                "objects": [{"path": op.object_path, "parameters": dict(op.parameters),
                             "references": list(op.references)} for op in report.plan.operations]}
    (output_dir / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
