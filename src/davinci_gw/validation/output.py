"""复核临时输出中的新增对象、完整语义、UUID、Handle ID 和内部引用。"""

from __future__ import annotations

from collections import Counter

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import OutputValidationError
from davinci_gw.domain.models import MutationAction, MutationKind, MutationPlan
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import definition_ref, operation_matches, semantic_values
from davinci_gw.routing.routing_group_membership import (
    RoutingGroupMembershipService,
    RoutingGroupModelError,
)

HANDLE_DEFINITIONS = (
    defs.CANIF_RX_HANDLE, defs.CANIF_TX_HANDLE, defs.PDUR_SRC_HANDLE, defs.PDUR_DEST_HANDLE,
)


def validate_generated_output(document: ArxmlDocument, plan: MutationPlan) -> None:
    """验证计划操作已完整落盘，且新引用在输出文件中唯一可解析。"""
    document.inspect()
    index = document.build_index()
    routing_groups = RoutingGroupMembershipService(document, index)
    failures: list[str] = []
    recreated_paths = {
        operation.object_path for operation in plan.operations
        if operation.action is MutationAction.CREATE
    }
    recreated_memberships = {
        (operation.parent_path, operation.definition_ref, operation.references)
        for operation in plan.operations
        if operation.action is MutationAction.ADD_REFERENCE
    }
    for operation in plan.operations:
        if operation.action in {MutationAction.ADD_REFERENCE, MutationAction.REMOVE_REFERENCE}:
            target = operation.reference(defs.PDUR_ROUTING_GROUP_DEST_REF)
            if target is None:
                failures.append(f"路由组成员操作“{operation.parent_path}”缺少目标引用")
                continue
            try:
                count = routing_groups.reference_count(operation.parent_path, target)
            except RoutingGroupModelError as exc:
                failures.append(str(exc))
                continue
            identity = (operation.parent_path, operation.definition_ref, operation.references)
            if (operation.action is MutationAction.REMOVE_REFERENCE
                    and identity in recreated_memberships):
                # DELETE→ADD 替换对的最终投影必须保留一条引用；后续 ADD 分支会验证其唯一性。
                continue
            expected = 1 if operation.action is MutationAction.ADD_REFERENCE else 0
            if count != expected:
                failures.append(
                    f"路由组“{operation.parent_path}”到“{target}”的成员引用实际为{count}个，"
                    f"计划为{expected}个"
                )
            if operation.action is MutationAction.ADD_REFERENCE and len(index.find_by_path(target)) != 1:
                failures.append(f"新增路由组成员目标“{target}”无法在输出中唯一解析")
            continue
        if operation.action is MutationAction.REMOVE:
            # DELETE＋ADD 替换对允许同一路径在投影上被重新创建；CREATE 语义在后续分支验证。
            if operation.object_path in recreated_paths:
                continue
            found = index.find_by_path(operation.object_path)
            if found:
                failures.append(f"计划删除对象“{operation.object_path}”仍存在{len(found)}个")
            if index.find_referrers(operation.object_path):
                failures.append(f"计划删除对象“{operation.object_path}”仍被输出中的对象引用")
            continue
        if operation.action is MutationAction.REMOVE_PARAMETERS:
            found = index.find_by_path(operation.object_path)
            if len(found) != 1:
                failures.append(f"参数删除对象“{operation.object_path}”实际找到{len(found)}个")
                continue
            actual, _ = semantic_values(found[0], document.namespace)
            for definition, _ in operation.parameters:
                if definition in actual:
                    failures.append(f"计划删除参数“{definition}”仍存在于“{operation.object_path}”")
            continue
        if operation.action is MutationAction.UPSERT_PARAMETERS:
            found = index.find_by_path(operation.parent_path)
            if len(found) != 1:
                failures.append(f"ComSignal 参数对象“{operation.parent_path}”实际找到{len(found)}个")
                continue
            actual, _ = semantic_values(found[0], document.namespace)
            for definition, value in operation.parameters:
                if actual.get(definition) != (value,):
                    failures.append(f"ComSignal 参数“{definition}”未按计划写入“{value}”")
            continue
        if operation.action is MutationAction.RETAIN:
            found = index.find_by_path(operation.object_path)
            if len(found) != 1:
                failures.append(f"计划保留对象“{operation.object_path}”实际找到{len(found)}个")
            elif operation.parameters or operation.references:
                recursive = operation.kind in {
                    MutationKind.COM_GW_SOURCE, MutationKind.COM_GW_DESTINATION,
                }
                if not operation_matches(
                    found[0], operation, document.namespace, recursive=recursive,
                ):
                    failures.append(f"计划保留对象“{operation.object_path}”语义发生变化")
            continue
        found = index.find_by_path(operation.object_path)
        recursive = operation.kind in {MutationKind.COM_GW_SOURCE, MutationKind.COM_GW_DESTINATION}
        if len(found) != 1:
            failures.append(f"计划对象“{operation.object_path}”实际找到{len(found)}个")
        elif not operation_matches(found[0], operation, document.namespace, recursive=recursive):
            failures.append(f"计划对象“{operation.object_path}”参数或引用与计划不一致")
        for _, target in operation.references:
            if len(index.find_by_path(target)) != 1:
                failures.append(f"对象“{operation.object_path}”的内部引用“{target}”无法唯一解析")

    for decision in plan.decisions:
        if len(index.find_by_path(decision.object_path)) != 1:
            failures.append(
                f"计划保留对象“{decision.object_path}”未在输出中唯一保留：{decision.reason}"
            )

    output_uuids = Counter(
        node.get("UUID") for node in document.root.iter() if node.get("UUID")
    )
    for value in plan.expected_new_uuids:
        if output_uuids[value] != 1:
            failures.append(
                f"本次新增确定性UUID“{value}”在输出中出现{output_uuids[value]}次，计划为唯一值"
            )
    planned_handles: Counter[tuple[str, str]] = Counter()
    for operation in plan.operations:
        if operation.action is not MutationAction.CREATE:
            continue
        for definition, value in operation.parameters:
            if definition in HANDLE_DEFINITIONS:
                planned_handles[(definition, value)] += 1

    output_handles: Counter[tuple[str, str]] = Counter()
    handle_definitions = frozenset(HANDLE_DEFINITIONS)
    for node in document.root.iter():
        definition = definition_ref(node, document.namespace)
        if definition not in handle_definitions:
            continue
        value = node.find(f"{{{document.namespace}}}VALUE")
        if value is not None and value.text is not None:
            output_handles[(definition, value.text.strip())] += 1
    for handle, planned_count in planned_handles.items():
        # 分配器保证计划值在基线中未占用，因此输出计数必须恰好等于本次新增数。
        # 既有工程中的历史重复值不属于本次生成范围，不在此误报。
        if output_handles[handle] != planned_count:
            definition, value = handle
            failures.append(
                f"本次新增Handle ID“{definition}={value}”在输出中出现"
                f"{output_handles[handle]}次，计划为{planned_count}次"
            )
    if failures:
        raise OutputValidationError("输出ARXML验证失败：" + "；".join(failures[:10]) + "。")
