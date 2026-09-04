"""第07轮 PduR 路由组成员关系的真实结构、安全边界和幂等测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import etree

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.models import MutationAction, MutationKind, SourceLocation
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, semantic_values
from davinci_gw.routing.routing_group_membership import (
    RoutingGroupMembershipRequest,
    RoutingGroupMembershipService,
    _GroupState,
    _MemberEntry,
)
from tests.conftest import direct_row

DESTINATION = (
    "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/"
    "GWT_SRC_MSG_100_SRC/DST_MSG_200_DST"
)
SECOND_DESTINATION = (
    "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/"
    "GWT_SRC_MSG_100_SRC/DST_MSG_2_201_DST"
)
EXISTING_DESTINATION = (
    "/Cfg/PduR/PduRRoutingTables/PduRRoutingTable/ExistingPath/ExistingDest"
)


def _messages(report: object) -> str:
    return "\n".join(issue.message for issue in report.all_issues)


def _group_node(document: ArxmlDocument, name: str) -> etree._Element:
    candidates = tuple(
        node for node in document.build_index().find_by_short_name(name)
        if definition_ref(node, document.namespace) == defs.PDUR_ROUTING_GROUP
    )
    assert len(candidates) == 1
    return candidates[0]


def _members(path: Path, group_name: str) -> tuple[str, ...]:
    document = ArxmlDocument.load(path)
    _, references = semantic_values(_group_node(document, group_name), document.namespace)
    return references.get(defs.PDUR_ROUTING_GROUP_DEST_REF, ())


def _generate_add(
    workbook_factory, arxml_factory, tmp_path: Path, *,
    routing_groups: dict[str, tuple[str, ...]] | None = None,
    name: str = "routing_group_add",
) -> Path:
    output = tmp_path / f"{name}.arxml"
    report = generate_inputs(
        workbook_factory(
            filename=f"{name}_v4.84.xlsx",
            direct_rows=(direct_row(),),
            signal_rows=(),
        ),
        arxml_factory(filename=f"{name}_base.arxml", routing_groups=routing_groups),
        output,
    )
    assert report.is_success, _messages(report)
    return output


def test_single_group_add_and_adapter_recognition(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    output = _generate_add(workbook_factory, arxml_factory, tmp_path)
    assert set(_members(output, "DefaultRoutingGroup")) == {
        DESTINATION, EXISTING_DESTINATION,
    }
    document = ArxmlDocument.load(output)
    service = RoutingGroupMembershipService(document)
    assert service.groups_for_destination(DESTINATION) == ("DefaultRoutingGroup",)
    parameters, _ = semantic_values(_group_node(document, "DefaultRoutingGroup"), document.namespace)
    assert parameters[defs.PDUR_ROUTING_GROUP_ENABLED] == ("true",)
    assert parameters[defs.PDUR_ROUTING_GROUP_ID] == ("1",)


def test_two_groups_with_same_main_channel_block_add(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    groups = {
        "DefaultRoutingGroup": (EXISTING_DESTINATION,),
        "AuxRoutingGroup": (EXISTING_DESTINATION,),
    }
    report = generate_inputs(
        workbook_factory(filename="ambiguous_main_v4.84.xlsx", signal_rows=()),
        arxml_factory(filename="ambiguous_main.arxml", routing_groups=groups),
        tmp_path / "ambiguous_main_output.arxml",
    )
    assert not report.is_success
    assert "PDUR_ROUTING_GROUP_CHANNEL_AMBIGUOUS" in {issue.code for issue in report.errors}


def test_channel_conflict_uses_application_group_name_only_as_cross_check(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    """多个结构候选同属一通道时，只用名称交叉确认唯一正式应用组。"""
    groups = {
        "PduRRoutingPathGroup_DSTCanApp": (EXISTING_DESTINATION,),
        "PduRRoutingPathGroup_TEST_200_DSTCan": (EXISTING_DESTINATION,),
    }
    output = tmp_path / "formal_and_special_group.arxml"
    report = generate_inputs(
        workbook_factory(filename="formal_and_special_v4.84.xlsx", signal_rows=()),
        arxml_factory(filename="formal_and_special_base.arxml", routing_groups=groups),
        output,
    )

    assert report.is_success, _messages(report)
    assert DESTINATION in _members(output, "PduRRoutingPathGroup_DSTCanApp")
    assert DESTINATION not in _members(output, "PduRRoutingPathGroup_TEST_200_DSTCan")
    assert "PDUR_ROUTING_GROUP_SECONDARY_GROUP_IGNORED" in {
        issue.code for issue in report.warnings
    }


def test_existing_route_add_repairs_missing_membership_but_delete_blocks(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = _generate_add(workbook_factory, arxml_factory, tmp_path, name="missing_relation_base")
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    group = next(
        node for node in tree.getroot().iter()
        if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP
    )
    values = group.find(f"{{{namespace}}}REFERENCE-VALUES")
    removed = next(
        entry for entry in values
        if entry.find(f"{{{namespace}}}VALUE-REF").text == DESTINATION
    )
    values.remove(removed)
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)

    repaired = tmp_path / "missing_relation_repaired.arxml"
    add_report = generate_inputs(
        workbook_factory(filename="missing_relation_add_v4.84.xlsx", signal_rows=()),
        baseline, repaired,
    )
    assert add_report.is_success, _messages(add_report)
    assert add_report.plan.direct_existing_count == 1
    assert any(
        operation.action is MutationAction.ADD_REFERENCE
        for operation in add_report.plan.operations
    )
    assert _members(repaired, "DefaultRoutingGroup").count(DESTINATION) == 1

    blocked = tmp_path / "missing_relation_delete.arxml"
    delete_report = generate_inputs(
        workbook_factory(
            filename="missing_relation_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ), baseline, blocked,
    )
    assert not delete_report.is_success and not blocked.exists()
    assert "PDUR_ROUTING_GROUP_MEMBER_NOT_FOUND" in {
        issue.code for issue in delete_report.errors
    }


def test_add_blocks_existing_destination_in_wrong_group(
    arxml_factory, monkeypatch,
) -> None:
    """既有 DestPdu 的实际组与目标通道主组不一致时不能自动迁移。"""
    document = ArxmlDocument.load(arxml_factory(filename="wrong_group_add.arxml"))
    index = document.build_index()
    service = RoutingGroupMembershipService(document, index)
    group_node = _group_node(document, "DefaultRoutingGroup")
    destination_node = index.find_by_path(EXISTING_DESTINATION)[0]
    expected = _GroupState("ExpectedApp", "/Cfg/PduR/ExpectedApp", group_node, ())
    wrong = _GroupState(
        "WrongOrDiagnosticGroup", "/Cfg/PduR/WrongOrDiagnosticGroup", group_node,
        (_MemberEntry(destination_node, EXISTING_DESTINATION),),
    )
    monkeypatch.setattr(service, "_group_for_request", lambda request: (expected, ()))
    monkeypatch.setattr(service, "_all_groups", lambda source: ((expected, wrong), ()))

    plan = service.plan_add(RoutingGroupMembershipRequest(
        "DST_CAN", "/Cfg/CanIf/CanIfInitCfg/TX2", EXISTING_DESTINATION,
        SourceLocation("直接报文路由", 2),
    ))
    assert plan.operations == ()
    assert "PDUR_ROUTING_GROUP_CHANNEL_MISMATCH" in {
        problem.code for problem in plan.problems
    }


def test_single_group_delete_removes_reference_before_destination(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = _generate_add(workbook_factory, arxml_factory, tmp_path, name="single_delete_base")
    output = tmp_path / "single_delete.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="single_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ), baseline, output,
    )
    assert report.is_success, _messages(report)
    actions = [operation.action for operation in report.plan.operations]
    assert actions.index(MutationAction.REMOVE_REFERENCE) < actions.index(MutationAction.REMOVE)
    assert DESTINATION not in _members(output, "DefaultRoutingGroup")
    assert EXISTING_DESTINATION in _members(output, "DefaultRoutingGroup")
    assert not ArxmlDocument.load(output).build_index().find_by_path(DESTINATION)


def test_two_groups_with_same_main_channel_block_delete(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    groups = {
        "DefaultRoutingGroup": (EXISTING_DESTINATION,),
        "AuxRoutingGroup": (EXISTING_DESTINATION,),
    }
    baseline = arxml_factory(filename="multi_delete_base.arxml", routing_groups=groups)
    delete_config = workbook_factory(
        filename="multi_delete_v4.84.xlsx",
        direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
    )
    first = tmp_path / "multi_delete_first.arxml"
    first_report = generate_inputs(delete_config, baseline, first)
    assert not first_report.is_success
    assert "PDUR_ROUTING_GROUP_CHANNEL_AMBIGUOUS" in {
        issue.code for issue in first_report.errors
    }


def test_missing_and_ambiguous_group_block_output(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    missing_output = tmp_path / "missing_group.arxml"
    missing = generate_inputs(
        workbook_factory(
            filename="missing_group_v4.84.xlsx",
            direct_rows=(direct_row(),), signal_rows=(),
        ), arxml_factory(routing_groups={}), missing_output,
    )
    assert not missing.is_success and not missing_output.exists()
    assert "PDUR_APPLICATION_ROUTING_GROUP_NOT_FOUND" in {issue.code for issue in missing.errors}
    assert DESTINATION in _messages(missing) and "第2行" in _messages(missing)

    duplicate_baseline = arxml_factory(filename="ambiguous_group_base.arxml")
    tree = etree.parse(str(duplicate_baseline))
    namespace = etree.QName(tree.getroot()).namespace
    group = next(
        node for node in tree.getroot().iter()
        if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP
    )
    clone = deepcopy(group)
    clone.set("UUID", "00000000-0000-0000-0000-999999999999")
    group.getparent().append(clone)
    tree.write(str(duplicate_baseline), encoding="UTF-8", xml_declaration=True)
    ambiguous_output = tmp_path / "ambiguous_group.arxml"
    ambiguous = generate_inputs(
        workbook_factory(filename="ambiguous_group_v4.84.xlsx", signal_rows=()),
        duplicate_baseline, ambiguous_output,
    )
    assert not ambiguous.is_success and not ambiguous_output.exists()
    assert "PDUR_ROUTING_GROUP_AMBIGUOUS" in {issue.code for issue in ambiguous.errors}


def test_multiple_existing_group_membership_blocks_delete(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    groups = {
        "DefaultRoutingGroup": (EXISTING_DESTINATION,),
        "AuxRoutingGroup": (EXISTING_DESTINATION,),
    }
    baseline = arxml_factory(filename="multiple_membership_base.arxml", routing_groups=groups)
    output = tmp_path / "undeclared_delete.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="undeclared_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ), baseline, output,
    )
    assert not report.is_success and not output.exists()
    assert "PDUR_ROUTING_GROUP_CHANNEL_AMBIGUOUS" in {issue.code for issue in report.errors}


def test_delete_last_member_blocks_empty_group(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = _generate_add(workbook_factory, arxml_factory, tmp_path, name="empty_group_base")
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    group = next(node for node in tree.getroot().iter()
                 if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP)
    values = group.find(f"{{{namespace}}}REFERENCE-VALUES")
    values.remove(next(entry for entry in values
                       if entry.find(f"{{{namespace}}}VALUE-REF").text == EXISTING_DESTINATION))
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "empty_group_delete.arxml"
    report = generate_inputs(
        workbook_factory(
            filename="empty_group_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ), baseline, output,
    )
    assert not report.is_success and not output.exists()
    assert "PDUR_ROUTING_GROUP_WOULD_BE_EMPTY" in {issue.code for issue in report.errors}


def test_duplicate_dangling_and_unsupported_members_block_output(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    for kind in ("duplicate", "dangling", "unsupported"):
        baseline = arxml_factory(filename=f"{kind}_member_base.arxml")
        tree = etree.parse(str(baseline))
        namespace = etree.QName(tree.getroot()).namespace
        group = next(
            node for node in tree.getroot().iter()
            if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP
        )
        values = group.find(f"{{{namespace}}}REFERENCE-VALUES")
        entry = values[0]
        if kind == "duplicate":
            values.append(deepcopy(entry))
            expected_code = "PDUR_ROUTING_GROUP_MEMBER_DUPLICATE"
        elif kind == "dangling":
            entry.find(f"{{{namespace}}}VALUE-REF").text = "/Cfg/PduR/MissingDest"
            expected_code = "PDUR_ROUTING_GROUP_MEMBER_DANGLING"
        else:
            entry.find(f"{{{namespace}}}VALUE-REF").set("DEST", "UNSUPPORTED-TARGET")
            expected_code = "PDUR_ROUTING_GROUP_MODEL_UNSUPPORTED"
        tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
        output = tmp_path / f"{kind}_member_output.arxml"
        report = generate_inputs(
            workbook_factory(filename=f"{kind}_member_v4.84.xlsx", signal_rows=()),
            baseline, output,
        )
        assert not report.is_success and not output.exists()
        assert expected_code in {issue.code for issue in report.errors}


def test_same_name_with_other_group_definition_is_explicitly_unsupported(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    baseline = arxml_factory(filename="other_group_model_base.arxml")
    tree = etree.parse(str(baseline))
    namespace = etree.QName(tree.getroot()).namespace
    group = next(
        node for node in tree.getroot().iter()
        if definition_ref(node, namespace) == defs.PDUR_ROUTING_GROUP
    )
    group.find(f"{{{namespace}}}DEFINITION-REF").text = "/Vector/PduR/OtherRoutingPathGroup"
    tree.write(str(baseline), encoding="UTF-8", xml_declaration=True)
    output = tmp_path / "other_group_model_output.arxml"
    report = generate_inputs(
        workbook_factory(filename="other_group_model_v4.84.xlsx", signal_rows=()),
        baseline, output,
    )
    assert not report.is_success and not output.exists()
    assert "PDUR_APPLICATION_ROUTING_GROUP_NOT_FOUND" in {
        issue.code for issue in report.errors
    }


def test_one_to_many_delete_removes_only_selected_leg_and_membership(
    workbook_factory, arxml_factory, tmp_path: Path,
) -> None:
    second = direct_row(**{
        "目标网段报文名称": "DST_MSG_2", "目标网段报文CANID": "0x201",
    })
    baseline = tmp_path / "one_to_many_group_base.arxml"
    add_report = generate_inputs(
        workbook_factory(
            filename="one_to_many_group_add_v4.84.xlsx",
            direct_rows=(direct_row(), second), signal_rows=(),
        ), arxml_factory(), baseline,
    )
    assert add_report.is_success, _messages(add_report)
    output = tmp_path / "one_to_many_group_delete.arxml"
    delete_report = generate_inputs(
        workbook_factory(
            filename="one_to_many_group_delete_v4.84.xlsx",
            direct_rows=(direct_row(**{"操作类型": "DELETE"}),), signal_rows=(),
        ), baseline, output,
    )
    assert delete_report.is_success, _messages(delete_report)
    members = _members(output, "DefaultRoutingGroup")
    assert DESTINATION not in members
    assert SECOND_DESTINATION in members
    index = ArxmlDocument.load(output).build_index()
    assert not index.find_by_path(DESTINATION)
    assert len(index.find_by_path(SECOND_DESTINATION)) == 1
    assert any(
        operation.kind is MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP
        for operation in delete_report.plan.operations
    )
