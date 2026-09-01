"""统一组织 ADD 规划、跨模块依赖顺序、应用回滚和输出验证期望。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    MutationAction,
    MutationKind,
    MutationOperation,
    MutationPlan,
    ValidationIssue,
    WorkbookData,
)
from davinci_gw.modules.canif_editor import CanIfEditor
from davinci_gw.modules.com_editor import ComEditor
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.common import (
    MutationContext,
    build_container,
)
from davinci_gw.modules.ecuc_editor import EcucEditor
from davinci_gw.modules.pdur_editor import PduREditor
from davinci_gw.mutations import MutationHandler, MutationHandlerRegistry

from .direct_route import DirectRoutePlanner
from .routing_group_membership import RoutingGroupMembershipService
from .signal_route import SignalRoutePlanner

HANDLE_DEFINITIONS = frozenset({
    defs.CANIF_RX_HANDLE, defs.CANIF_TX_HANDLE, defs.PDUR_SRC_HANDLE, defs.PDUR_DEST_HANDLE,
})


def _without_handles(operation: MutationOperation) -> tuple[tuple[str, str], ...]:
    return tuple(item for item in operation.parameters if item[0] not in HANDLE_DEFINITIONS)


def _problem(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message)


class AddCoordinator:
    """持有一次解析树的四模块编辑器，确保先完整规划再进行任何修改。"""

    def __init__(
        self, document: ArxmlDocument, workbook: WorkbookData, index: ArxmlIndex | None = None,
        handler_registry: MutationHandlerRegistry | None = None,
    ) -> None:
        self.document = document
        self.workbook = workbook
        self.index = index or document.build_index()
        self.template_cache = {}
        self.ecuc = EcucEditor(document, self.index)
        self.canif = CanIfEditor(document, self.index)
        self.pdur = PduREditor(document, self.index)
        self.routing_groups = RoutingGroupMembershipService(
            document, self.index, reference_data=workbook.reference_data,
        )
        self.com = ComEditor(document, self.index)
        self.handlers = handler_registry or MutationHandlerRegistry((
            MutationHandler("ecuc", frozenset({MutationKind.ECUC_PDU}), 10, self.ecuc.apply),
            MutationHandler("canif_rx", frozenset({MutationKind.CANIF_RX_PDU}), 20, self.canif.apply),
            MutationHandler("canif_tx", frozenset({MutationKind.CANIF_TX_PDU}), 30, self.canif.apply),
            MutationHandler("pdur_path", frozenset({MutationKind.PDUR_ROUTING_PATH}), 40, self.pdur.apply),
            MutationHandler("pdur_src", frozenset({MutationKind.PDUR_SRC_PDU}), 50, self.pdur.apply),
            MutationHandler("pdur_dest", frozenset({MutationKind.PDUR_DEST_PDU}), 60, self.pdur.apply),
            MutationHandler(
                "pdur_group_membership",
                frozenset({MutationKind.PDUR_ROUTING_GROUP_MEMBERSHIP}),
                65,
                self.routing_groups.apply_add,
            ),
            MutationHandler("com_mapping", frozenset({MutationKind.COM_GW_MAPPING}), 70, self.com.apply),
            MutationHandler("com_source", frozenset({MutationKind.COM_GW_SOURCE}), 80, self.com.apply),
            MutationHandler(
                "com_destination", frozenset({MutationKind.COM_GW_DESTINATION}), 90, self.com.apply,
            ),
            MutationHandler(
                "com_signal_access", frozenset({MutationKind.COM_SIGNAL_ACCESS}), 95, self.com.apply,
            ),
            MutationHandler(
                "com_timeout", frozenset({MutationKind.COM_SIGNAL_TIMEOUT}), 100, self.com.apply,
            ),
        ))

    def _deduplicate_operations(
        self, operations: tuple[MutationOperation, ...], issues: list[ValidationIssue],
    ) -> tuple[MutationOperation, ...]:
        unique: dict[tuple[object, ...], MutationOperation] = {}
        for operation in operations:
            key = (operation.action,) + operation.identity
            previous = unique.get(key)
            if previous is None:
                unique[key] = operation
            elif (
                previous.definition_ref == operation.definition_ref
                and _without_handles(previous) == _without_handles(operation)
                and previous.references == operation.references
            ):
                locations = tuple(dict.fromkeys(previous.source_locations + operation.source_locations))
                unique[key] = replace(previous, source_locations=locations)
            else:
                issues.append(_problem(
                    "PLAN_PATH_CONFLICT",
                    f"两个ADD计划试图以不同内容创建或修改“{key[2]}”。请检查重复名称、通道和路由参数。",
                ))
        return self.handlers.sort_operations(tuple(unique.values()))

    def _preflight_operations(
        self, operations: tuple[MutationOperation, ...], issues: list[ValidationIssue],
    ) -> tuple[str, ...]:
        planned_paths = {operation.object_path for operation in operations
                         if operation.action is MutationAction.CREATE}
        baseline_uuids = Counter(node.get("UUID") for node in self.document.root.iter() if node.get("UUID"))
        planned_uuids: set[str] = set()
        for operation in operations:
            if operation.action is MutationAction.ADD_REFERENCE:
                owner = self.index.find_by_path(operation.parent_path)
                if len(owner) != 1:
                    issues.append(_problem(
                        "PLANNED_REFERENCE_OWNER_UNRESOLVED",
                        f"路由组成员操作的所属组“{operation.parent_path}”实际找到{len(owner)}个。",
                    ))
                candidate_uuids = ()
            elif operation.kind is MutationKind.COM_SIGNAL_ACCESS:
                self.com.ensure_access_template(operation)
                candidate_uuids = self.com.access_candidate_uuids(operation)
            elif operation.action is MutationAction.UPSERT_PARAMETERS:
                self.com.ensure_timeout_templates(operation)
                candidate_uuids = self.com.timeout_candidate_uuids(operation)
            else:
                candidate = build_container(
                    self.document.root, self.document.namespace, operation, index=self.index,
                    template_cache=self.template_cache,
                )
                candidate_uuids = tuple(
                    node.get("UUID") for node in candidate.iter() if node.get("UUID")
                )
            for value in candidate_uuids:
                if baseline_uuids[value] or value in planned_uuids:
                    issues.append(_problem(
                        "PLANNED_UUID_CONFLICT",
                        f"计划对象“{operation.object_path}”生成的确定性UUID“{value}”已被占用。",
                    ))
                planned_uuids.add(value)
            if operation.action in {MutationAction.UPSERT_PARAMETERS, MutationAction.ADD_REFERENCE}:
                for _, target_path in operation.references:
                    if target_path in planned_paths:
                        continue
                    found = self.index.find_by_path(target_path)
                    if len(found) != 1:
                        issues.append(_problem(
                            "PLANNED_REFERENCE_UNRESOLVED",
                            f"路由组成员引用“{target_path}”在基准和新增计划中均无法唯一解析。",
                        ))
                continue
            for _, target_path in operation.references:
                if target_path in planned_paths:
                    continue
                found = self.index.find_by_path(target_path)
                if len(found) != 1:
                    issues.append(_problem(
                        "PLANNED_REFERENCE_UNRESOLVED",
                        f"计划对象“{operation.object_path}”引用“{target_path}”，基准中实际找到{len(found)}个目标。"
                        "请修复引用对象后重试。",
                    ))
        return tuple(sorted(planned_uuids))

    def plan(self) -> MutationPlan:
        """规划直接报文与信号 ADD，并完成模板、UUID和内部引用预检。"""
        direct = DirectRoutePlanner(
            self.workbook, self.ecuc, self.canif, self.pdur, self.routing_groups,
        ).plan(
            tuple(route for route in self.workbook.direct_routes if route.operation.value == "ADD"),
        )
        signal = SignalRoutePlanner(self.workbook, self.com).plan(
            tuple(route for route in self.workbook.signal_routes if route.operation.value == "ADD"),
        )
        issues = list(direct.issues + signal.issues)
        operations = self._deduplicate_operations(direct.operations + signal.operations, issues)
        expected_new_uuids: tuple[str, ...] = ()
        if not any(issue.severity.value == "ERROR" for issue in issues):
            try:
                expected_new_uuids = self._preflight_operations(operations, issues)
            except ArxmlStructureError as exc:
                issues.append(_problem("ARXML_MUTATION_TEMPLATE_INVALID", str(exc)))
        return MutationPlan(
            operations=operations,
            issues=tuple(issues),
            direct_added_count=direct.added,
            direct_existing_count=direct.existing,
            direct_skipped_count=direct.skipped,
            signal_added_count=signal.added,
            signal_existing_count=signal.existing,
            signal_skipped_count=signal.skipped,
            expected_new_uuids=expected_new_uuids,
        )

    def apply(self, plan: MutationPlan) -> None:
        """按固定跨模块顺序应用已通过预检的计划；异常时回滚全部插入节点。"""
        if plan.errors:
            raise ArxmlStructureError("变更计划包含阻断错误，禁止修改ARXML树。")
        context = MutationContext(
            self.document.root, self.document.namespace, self.index,
            template_cache=self.template_cache,
        )
        try:
            self.handlers.apply_operations(context, plan.operations)
        except Exception:
            context.rollback()
            raise
