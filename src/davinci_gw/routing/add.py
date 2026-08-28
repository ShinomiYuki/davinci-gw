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

from .direct_route import DirectRoutePlanner
from .signal_route import SignalRoutePlanner

ORDER = {
    MutationKind.ECUC_PDU: 10,
    MutationKind.CANIF_RX_PDU: 20,
    MutationKind.CANIF_TX_PDU: 30,
    MutationKind.PDUR_ROUTING_PATH: 40,
    MutationKind.PDUR_SRC_PDU: 50,
    MutationKind.PDUR_DEST_PDU: 60,
    MutationKind.COM_GW_MAPPING: 70,
    MutationKind.COM_GW_SOURCE: 80,
    MutationKind.COM_GW_DESTINATION: 90,
    MutationKind.COM_SIGNAL_TIMEOUT: 100,
}

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
    ) -> None:
        self.document = document
        self.workbook = workbook
        self.index = index or document.build_index()
        self.template_cache = {}
        self.ecuc = EcucEditor(document, self.index)
        self.canif = CanIfEditor(document, self.index)
        self.pdur = PduREditor(document, self.index)
        self.com = ComEditor(document, self.index)

    def _deduplicate_operations(
        self, operations: tuple[MutationOperation, ...], issues: list[ValidationIssue],
    ) -> tuple[MutationOperation, ...]:
        unique: dict[tuple[MutationAction, MutationKind, str], MutationOperation] = {}
        for operation in operations:
            key = (operation.action, operation.kind, operation.object_path)
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
        return tuple(sorted(unique.values(), key=lambda item: (ORDER[item.kind], item.object_path)))

    def _preflight_operations(
        self, operations: tuple[MutationOperation, ...], issues: list[ValidationIssue],
    ) -> tuple[str, ...]:
        planned_paths = {operation.object_path for operation in operations
                         if operation.action is MutationAction.CREATE}
        baseline_uuids = Counter(node.get("UUID") for node in self.document.root.iter() if node.get("UUID"))
        planned_uuids: set[str] = set()
        for operation in operations:
            if operation.action is MutationAction.UPSERT_PARAMETERS:
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
            if operation.action is MutationAction.UPSERT_PARAMETERS:
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
        direct = DirectRoutePlanner(self.workbook, self.ecuc, self.canif, self.pdur).plan(
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
            for operation in plan.operations:
                if operation.kind is MutationKind.ECUC_PDU:
                    self.ecuc.apply(context, operation)
                elif operation.kind in {MutationKind.CANIF_RX_PDU, MutationKind.CANIF_TX_PDU}:
                    self.canif.apply(context, operation)
                elif operation.kind in {
                    MutationKind.PDUR_ROUTING_PATH, MutationKind.PDUR_SRC_PDU, MutationKind.PDUR_DEST_PDU,
                }:
                    self.pdur.apply(context, operation)
                else:
                    self.com.apply(context, operation)
        except Exception:
            context.rollback()
            raise
