"""第02轮信号路由 ADD、超时、缺失对象跳过和一对多测试。"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, semantic_values
from tests.conftest import signal_row


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def test_single_signal_route_and_source_timeout(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "signal.arxml"
    report = generate_inputs(workbook_factory(direct_rows=()), arxml_factory(), output)
    assert report.is_success, _messages(report)
    assert report.plan.signal_added_count == 1
    document = ArxmlDocument.load(output)
    index = document.build_index()
    mapping = index.find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")
    assert len(mapping) == 1
    source = index.find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert parameters[defs.COM_TIMEOUT_ACTION] == ("REPLACE",)
    assert parameters[defs.COM_TIMEOUT] == ("2",)
    assert defs.COM_TIMEOUT_SUBSTITUTION not in parameters


def test_signal_one_to_many_has_one_source_and_two_destinations(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    rows = (signal_row(), signal_row(**{"目标信号名": "DST_SIG_2"}))
    output = tmp_path / "signal_many.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=rows), arxml_factory(), output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_added_count == 2
    document = ArxmlDocument.load(output)
    mapping = document.build_index().find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")[0]
    group = mapping.find(f"{{{document.namespace}}}SUB-CONTAINERS")
    definitions = [definition_ref(child, document.namespace) for child in group]
    assert definitions.count(defs.COM_GW_SOURCE) == 1
    assert definitions.count(defs.COM_GW_DEST) == 2


def test_missing_target_signal_is_visible_skip_and_does_not_block_valid_target(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    rows = (signal_row(), signal_row(**{"目标信号名": "DBC_NOT_IMPORTED"}))
    output = tmp_path / "partial_signal.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=rows), arxml_factory(), output,
    )
    assert report.is_success, _messages(report)
    assert (report.plan.signal_added_count, report.plan.signal_skipped_count) == (1, 1)
    assert "DBC_NOT_IMPORTED" in _messages(report) and "已跳过" in _messages(report)


def test_ipdu_from_different_network_is_not_accepted(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "wrong_network.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(), signal_rows=(signal_row(**{"源网段": "OTHER"}),),
        ),
        arxml_factory(),
        output,
    )
    assert report.is_success
    assert report.plan.signal_skipped_count == 1
    assert "OTHER" in _messages(report) and "ComIPdu" in _messages(report)


def test_logical_lin_network_uses_unique_message_signal_relationship(
    workbook_factory: object, lin_target_arxml_factory: object, tmp_path: Path,
) -> None:
    """LIN 逻辑节点名无需等于物理通道名，但报文/信号结构候选必须唯一。"""
    output = tmp_path / "logical_lin.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(), signal_rows=(signal_row(**{"目标网段": "PSMM"}),),
        ),
        lin_target_arxml_factory(channels=("LIN04",)),
        output,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_added_count == 1
    document = ArxmlDocument.load(output)
    mapping = document.build_index().find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")[0]
    _, references = semantic_values(mapping, document.namespace, recursive=True)
    assert references[defs.COM_GW_DEST_SIGNAL_REF] == (
        "/Cfg/Com/ComConfig/DST_SIG_oDST_MSG_oLIN04_Tx",
    )


def test_logical_lin_network_with_duplicate_message_signal_pairs_is_blocked(
    workbook_factory: object, lin_target_arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "ambiguous_lin.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(), signal_rows=(signal_row(**{"目标网段": "PSMM"}),),
        ),
        lin_target_arxml_factory(channels=("LIN03", "LIN04")),
        output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "2 个结构候选" in _messages(report)


def test_logical_lin_fallback_is_blocked_when_same_pair_also_exists_on_can(
    workbook_factory: object, lin_target_arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "ambiguous_lin_can_pair.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(), signal_rows=(signal_row(**{"目标网段": "PSMM"}),),
        ),
        lin_target_arxml_factory(channels=("LIN04", "OTHERMessagelis")),
        output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "2 个结构候选" in _messages(report)


def test_all_missing_signal_routes_still_write_safe_copy_with_warning(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "all_skipped.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(signal_row(**{"目标信号名": "MISSING"}),)),
        arxml_factory(), output,
    )
    assert report.is_success
    assert report.plan.signal_skipped_count == 1
    assert output.exists()
    assert not ArxmlDocument.load(output).build_index().find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")


def test_empty_timeout_does_not_write_zero_or_timeout_parameter(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "no_timeout.arxml"
    report = generate_inputs(
        workbook_factory(direct_rows=(), signal_rows=(signal_row(**{"超时时间": None, "超时值": None}),)),
        arxml_factory(), output,
    )
    assert report.is_success, _messages(report)
    document = ArxmlDocument.load(output)
    source = document.build_index().find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
    parameters, _ = semantic_values(source, document.namespace)
    assert defs.COM_TIMEOUT not in parameters
    assert defs.COM_TIMEOUT_ACTION not in parameters


def test_standard_timeout_values_are_used_directly_without_cycle_time_inference(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    for label, standard_seconds in (("cycle_100ms", 2), ("cycle_over_100ms", 4)):
        output = tmp_path / f"generated_{label}.arxml"
        report = generate_inputs(
            workbook_factory(
                filename=f"{label}_v4.84.xlsx", direct_rows=(),
                signal_rows=(signal_row(**{"超时时间": standard_seconds}),),
            ), arxml_factory(filename=f"{label}.arxml"), output,
        )
        assert report.is_success, _messages(report)
        document = ArxmlDocument.load(output)
        source = document.build_index().find_by_path("/Cfg/Com/ComConfig/SRC_SIG_oSRC_MSG_oSRC_Rx")[0]
        parameters, _ = semantic_values(source, document.namespace)
        assert parameters[defs.COM_TIMEOUT] == (str(standard_seconds),)


def test_existing_timeout_conflict_blocks_without_partial_output(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    first = tmp_path / "first.arxml"
    assert generate_inputs(workbook_factory(direct_rows=()), arxml_factory(), first).is_success
    output = tmp_path / "conflict.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="changed_v4.84.xlsx", direct_rows=(),
            signal_rows=(signal_row(**{"超时时间": 4}),),
        ), first, output,
    )
    assert not report.is_success
    assert not output.exists()
    assert "超时参数" in _messages(report)


def test_lin_route_never_creates_linif_nodes(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    output = tmp_path / "lin.arxml"
    report = generate_inputs(
        workbook_factory(
            direct_rows=(),
            signal_rows=(signal_row(**{"源网段": "LIN", "目标网段": "LIN"}),),
        ), arxml_factory(), output,
    )
    assert report.is_success
    document = ArxmlDocument.load(output)
    assert not any("/LinIf" in (definition_ref(node, document.namespace) or "") for node in document.root.iter())


def test_signal_second_run_is_idempotent(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    config = workbook_factory(direct_rows=())
    first = tmp_path / "first.arxml"
    second = tmp_path / "second.arxml"
    assert generate_inputs(config, arxml_factory(), first).is_success
    report = generate_inputs(config, first, second)
    assert report.is_success, _messages(report)
    assert report.plan.signal_added_count == 0
    assert report.plan.signal_existing_count == 1


def test_existing_source_mapping_only_adds_missing_destination(
    workbook_factory: object, arxml_factory: object, tmp_path: Path,
) -> None:
    first = tmp_path / "one_destination.arxml"
    assert generate_inputs(workbook_factory(direct_rows=()), arxml_factory(), first).is_success

    second = tmp_path / "two_destinations.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="add_destination_v4.84.xlsx", direct_rows=(),
            signal_rows=(signal_row(**{"目标信号名": "DST_SIG_2"}),),
        ),
        first,
        second,
    )
    assert report.is_success, _messages(report)
    assert report.plan.signal_added_count == 1
    document = ArxmlDocument.load(second)
    mapping = document.build_index().find_by_path("/Cfg/Com/ComConfig/GWT_Sig_SRC_SIG_SRC")[0]
    group = mapping.find(f"{{{document.namespace}}}SUB-CONTAINERS")
    definitions = [definition_ref(child, document.namespace) for child in group]
    assert definitions.count(defs.COM_GW_SOURCE) == 1
    assert definitions.count(defs.COM_GW_DEST) == 2
