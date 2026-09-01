"""精确规划并阶段性应用直接报文与信号路由 DELETE。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import re

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.arxml.index import ArxmlIndex, autosar_path
from davinci_gw.arxml.namespace import direct_child_text, local_name, qualified
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    DirectRouteChange,
    MutationAction,
    MutationKind,
    MutationOperation,
    MutationPlan,
    OperationType,
    RetentionDecision,
    SignalRouteChange,
    SourceLocation,
    ValidationIssue,
    ValidationSeverity,
    WorkbookData,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.canif_editor import CanIfEditor
from davinci_gw.modules.com_editor import ComEditor
from davinci_gw.modules.common import definition_ref, semantic_values, unique_named_node
from davinci_gw.modules.ecuc_editor import EcucEditor
from davinci_gw.modules.pdur_editor import PduREditor

from .routing_group_membership import (
    RoutingGroupMembershipProblem,
    RoutingGroupMembershipRequest,
    RoutingGroupMembershipService,
)


@dataclass(frozen=True, slots=True)
class _DirectMatch:
    route: DirectRouteChange
    path_path: str
    destination_path: str
    source_pdur_path: str
    source_ecuc_path: str
    source_canif_path: str
    target_ecuc_path: str
    target_canif_path: str


@dataclass(frozen=True, slots=True)
class _SignalMatch:
    route: SignalRouteChange
    mapping_path: str
    destination_path: str
    source_container_path: str
    source_signal_path: str
    target_signal_path: str


def _identity(route: DirectRouteChange | SignalRouteChange) -> str:
    if isinstance(route, DirectRouteChange):
        key = route.key
        return (f"{key.source_message_name}/{key.source_can_id:#X}/{key.source_channel} → "
                f"{key.target_message_name}/{key.target_can_id:#X}/{key.target_channel}")
    key = route.key
    return (f"{key.source_network}/{key.source_message_name}/{key.source_signal_name} → "
            f"{key.target_network}/{key.target_message_name}/{key.target_signal_name}")


def _issue(
    workbook: WorkbookData,
    route: DirectRouteChange | SignalRouteChange,
    code: str,
    detail: str,
    *,
    warning: bool = False,
) -> ValidationIssue:
    location = route.source
    return ValidationIssue(
        code=code,
        message=(f"配置表“{workbook.path}”的“{location.sheet_name}”工作表第{location.row_number}行，"
                 f"路由“{_identity(route)}”：{detail}"),
        severity=ValidationSeverity.WARNING if warning else ValidationSeverity.ERROR,
        file_path=workbook.path,
        location=location,
    )


def _membership_issue(
    workbook: WorkbookData,
    route: DirectRouteChange,
    problem: RoutingGroupMembershipProblem,
    baseline_path: Path,
) -> ValidationIssue:
    """基线警告只归属 ARXML；路由成员错误仍定位到对应 Excel 行。"""
    if problem.warning:
        return ValidationIssue(
            code=problem.code,
            message=problem.message,
            severity=ValidationSeverity.WARNING,
            file_path=baseline_path,
            location=SourceLocation(),
        )
    return _issue(workbook, route, problem.code, problem.message)


def _parent_and_name(path: str) -> tuple[str, str]:
    parent, _, name = path.rstrip("/").rpartition("/")
    return parent or "/", name


def _container_owner(node: etree._Element) -> etree._Element | None:
    current: etree._Element | None = node
    while current is not None:
        if isinstance(current.tag, str) and local_name(current) == "ECUC-CONTAINER-VALUE":
            return current
        current = current.getparent()
    return None


def _is_removed(path: str, removed_paths: set[str]) -> bool:
    return any(path == removed or path.startswith(f"{removed}/") for removed in removed_paths)


def _remaining_referrers(
    index: ArxmlIndex, namespace: str, target_path: str, removed_paths: set[str],
    *,
    expected_definition: str | None = None,
    removed_referrers: frozenset[etree._Element] = frozenset(),
) -> tuple[etree._Element, ...]:
    """按计划删除后的投影计算引用，绝不读取逐路由修改后的陈旧索引。"""
    remaining: list[etree._Element] = []
    for referrer in index.find_referrers(target_path):
        if referrer in removed_referrers:
            continue
        owner = _container_owner(referrer)
        if owner is None:
            continue
        owner_path = autosar_path(owner, namespace)
        if _is_removed(owner_path, removed_paths):
            continue
        if expected_definition is not None and definition_ref(owner, namespace) != expected_definition:
            continue
        remaining.append(owner)
    return tuple(remaining)


def _remaining_subtree_referrers(
    index: ArxmlIndex,
    namespace: str,
    node: etree._Element,
    removed_paths: set[str],
    *,
    removed_referrers: frozenset[etree._Element] = frozenset(),
) -> tuple[etree._Element, ...]:
    """返回计划删除一个完整子树后，仍指向其中任一命名对象的外部容器。"""
    remaining: list[etree._Element] = []
    seen: set[etree._Element] = set()
    for candidate in node.iter():
        if direct_child_text(candidate, namespace, "SHORT-NAME") is None:
            continue
        target_path = autosar_path(candidate, namespace)
        for owner in _remaining_referrers(
            index, namespace, target_path, removed_paths,
            removed_referrers=removed_referrers,
        ):
            if owner not in seen:
                seen.add(owner)
                remaining.append(owner)
    return tuple(remaining)


def _direct_subcontainers(node: etree._Element, namespace: str) -> tuple[etree._Element, ...]:
    """返回对象的直接 ECUC 子容器，用于识别不能连带删除的人工扩展。"""
    group = node.find(qualified(namespace, "SUB-CONTAINERS"))
    return tuple(list(group)) if group is not None else ()


def _subcontainers(node: etree._Element, namespace: str, definition: str) -> tuple[etree._Element, ...]:
    group = node.find(qualified(namespace, "SUB-CONTAINERS"))
    return tuple(child for child in list(group) if definition_ref(child, namespace) == definition) \
        if group is not None else ()


def _semantics_match(
    node: etree._Element,
    namespace: str,
    parameters: dict[str, str],
    references: dict[str, str],
) -> bool:
    actual_parameters, actual_references = semantic_values(node, namespace)
    return all(actual_parameters.get(key) == (value,) for key, value in parameters.items()) and all(
        actual_references.get(key) == (value,) for key, value in references.items()
    )


def _message_identity_matches(short_name: str | None, message_name: str) -> bool:
    """按下划线分隔的完整报文名匹配，不依赖 GWT、GWH、Gw 等项目命名前缀。"""
    if not short_name:
        return False
    return re.search(
        rf"(?:^|_){re.escape(message_name)}(?=_|$)", short_name,
    ) is not None


def _node_operation(
    node: etree._Element,
    namespace: str,
    kind: MutationKind,
    action: MutationAction,
    locations: tuple[SourceLocation, ...],
    *, preserve_semantics: bool = False,
) -> MutationOperation:
    path = autosar_path(node, namespace)
    parent, name = _parent_and_name(path)
    parameters: tuple[tuple[str, str], ...] = ()
    references: tuple[tuple[str, str], ...] = ()
    if preserve_semantics:
        recursive = kind in {MutationKind.COM_GW_SOURCE, MutationKind.COM_GW_DESTINATION}
        actual_parameters, actual_references = semantic_values(
            node, namespace, recursive=recursive,
        )
        parameters = tuple((key, value) for key in sorted(actual_parameters)
                           for value in actual_parameters[key])
        references = tuple((key, value) for key in sorted(actual_references)
                           for value in actual_references[key])
    return MutationOperation(
        kind, parent, name, definition_ref(node, namespace) or "", action=action,
        parameters=parameters, references=references, source_locations=locations,
    )


class DeleteCoordinator:
    """在共享基线索引上生成完整删除计划，并在阶段边界一次性应用。"""

    def __init__(
        self, document: ArxmlDocument, workbook: WorkbookData, index: ArxmlIndex | None = None,
    ) -> None:
        self.document = document
        self.workbook = workbook
        self.index = index or document.build_index()
        self.ecuc = EcucEditor(document, self.index)
        self.canif = CanIfEditor(document, self.index)
        self.pdur = PduREditor(document, self.index)
        self.routing_groups = RoutingGroupMembershipService(
            document, self.index, reference_data=workbook.reference_data,
        )
        self.com = ComEditor(document, self.index)
        self.references = {entry.channel_name: entry for entry in workbook.reference_data}
        self.operations: dict[tuple[object, ...], MutationOperation] = {}
        self.decisions: dict[tuple[str, str, str], RetentionDecision] = {}
        self.issues: list[ValidationIssue] = []

    def _add_operation(self, operation: MutationOperation) -> None:
        key = (operation.action,) + operation.identity
        previous = self.operations.get(key)
        if previous is None:
            self.operations[key] = operation
        elif replace(previous, source_locations=()) == replace(operation, source_locations=()):
            locations = previous.source_locations + tuple(
                location for location in operation.source_locations
                if location not in previous.source_locations
            )
            self.operations[key] = replace(previous, source_locations=locations)
        else:
            self.issues.append(ValidationIssue(
                code="DELETE_PLAN_PATH_CONFLICT",
                message=f"删除计划对“{operation.object_path}”产生不同预期，无法安全执行。请检查重复路由。",
            ))

    def _retain(
        self, path: str, reason: str, category: str, locations: tuple[SourceLocation, ...],
    ) -> None:
        self.decisions[(path, reason, category)] = RetentionDecision(path, reason, category, locations)

    def _unique_path(
        self,
        path: str,
        definition: str,
        route: DirectRouteChange,
        role: str,
    ) -> etree._Element | None:
        found = self.index.find_by_path(path)
        if len(found) != 1 or definition_ref(found[0], self.document.namespace) != definition:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_OBJECT_AMBIGUOUS",
                f"{role}“{path}”期望唯一且定义为“{definition}”，实际候选数为{len(found)}。"
                "请在 DaVinci 中确认对象归属后重试。",
            ))
            return None
        return found[0]

    def _channel_path(
        self, route: DirectRouteChange, *, source: bool,
    ) -> str | None:
        channel = route.key.source_channel if source else route.key.target_channel
        entry = self.references.get(channel)
        role = "源" if source else "目标"
        if entry is None:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_CHANNEL_MISSING",
                f"{role}通道“{channel}”未在引用数据中定义，无法核对删除归属。请补充精确通道映射。",
            ))
            return None
        name = entry.hrh_name if source else entry.tx_buffer_name
        definition = defs.CANIF_HRH if source else defs.CANIF_BUFFER
        if not name:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_CHANNEL_REFERENCE_MISSING",
                f"{role}通道缺少{'CanIfHrh名称' if source else 'CanIfTxBuffer名称'}，无法安全删除。",
            ))
            return None
        state, node = unique_named_node(self.index, self.document.namespace, name, definition)
        if state != "FOUND" or node is None:
            count = len(tuple(candidate for candidate in self.index.find_by_short_name(name)
                              if definition_ref(candidate, self.document.namespace) == definition))
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_CHANNEL_REFERENCE_AMBIGUOUS",
                f"通道引用“{name}”实际候选数为{count}，无法确认删除对象属于该通道。"
                "请修复引用数据或 DaVinci 配置后重试。",
            ))
            return None
        return autosar_path(node, self.document.namespace)

    def _match_direct(self, route: DirectRouteChange) -> _DirectMatch | None:
        key = route.key
        hrh_path = self._channel_path(route, source=True)
        buffer_path = self._channel_path(route, source=False)
        if hrh_path is None or buffer_path is None:
            return None

        source_chains: dict[tuple[str, str, str], tuple[etree._Element, etree._Element, etree._Element]] = {}
        source_semantic_candidates: list[str] = []
        for canif_node in self.index.find_by_definition_ref(defs.CANIF_RX):
            name = direct_child_text(canif_node, self.document.namespace, "SHORT-NAME")
            parameters, refs = semantic_values(canif_node, self.document.namespace)
            if (parameters.get(defs.CANIF_RX_CAN_ID) != (str(key.source_can_id),)
                    or refs.get(defs.CANIF_RX_HRH_REF) != (hrh_path,)
                    or len(refs.get(defs.CANIF_RX_PDU_REF, ())) != 1):
                continue
            source_semantic_candidates.append(autosar_path(canif_node, self.document.namespace))
            if not _message_identity_matches(name, key.source_message_name):
                continue
            source_ecuc_path = refs[defs.CANIF_RX_PDU_REF][0]
            ecuc_nodes = self.index.find_by_path(source_ecuc_path)
            if len(ecuc_nodes) != 1 or definition_ref(ecuc_nodes[0], self.document.namespace) != defs.ECUC_PDU:
                continue
            for referrer in self.index.find_referrers(source_ecuc_path):
                source_node = _container_owner(referrer)
                if source_node is None or definition_ref(source_node, self.document.namespace) != defs.PDUR_SRC:
                    continue
                if not _semantics_match(
                    source_node, self.document.namespace,
                    {defs.PDUR_SRC_DIRECTION: "RECEIVE"},
                    {defs.PDUR_SRC_PDU_REF: source_ecuc_path},
                ):
                    continue
                subcontainers = source_node.getparent()
                path_node = subcontainers.getparent() if subcontainers is not None else None
                if path_node is None or definition_ref(path_node, self.document.namespace) != defs.PDUR_PATH:
                    continue
                source_chains[(
                    autosar_path(canif_node, self.document.namespace),
                    autosar_path(source_node, self.document.namespace),
                    autosar_path(path_node, self.document.namespace),
                )] = (canif_node, source_node, path_node)

        target_chains: dict[tuple[str, str], tuple[etree._Element, str]] = {}
        target_semantic_candidates: list[tuple[str, str]] = []
        for canif_node in self.index.find_by_definition_ref(defs.CANIF_TX):
            name = direct_child_text(canif_node, self.document.namespace, "SHORT-NAME")
            parameters, refs = semantic_values(canif_node, self.document.namespace)
            if (parameters.get(defs.CANIF_TX_CAN_ID) != (str(key.target_can_id),)
                    or refs.get(defs.CANIF_TX_BUFFER_REF) != (buffer_path,)
                    or len(refs.get(defs.CANIF_TX_PDU_REF, ())) != 1):
                continue
            target_semantic_candidates.append((
                autosar_path(canif_node, self.document.namespace), refs[defs.CANIF_TX_PDU_REF][0],
            ))
            if not _message_identity_matches(name, key.target_message_name):
                continue
            target_ecuc_path = refs[defs.CANIF_TX_PDU_REF][0]
            ecuc_nodes = self.index.find_by_path(target_ecuc_path)
            if len(ecuc_nodes) != 1 or definition_ref(ecuc_nodes[0], self.document.namespace) != defs.ECUC_PDU:
                continue
            target_chains[(
                autosar_path(canif_node, self.document.namespace), target_ecuc_path,
            )] = (canif_node, target_ecuc_path)

        if source_semantic_candidates and not source_chains:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_SOURCE_SEMANTIC_CONFLICT",
                f"源 CAN ID 0x{key.source_can_id:X} 与 HRH“{hrh_path}”命中对象"
                f" {source_semantic_candidates}，但报文名“{key.source_message_name}”不匹配或"
                " PduRSrcPdu 引用链不完整；为避免误删，本次阻断。",
            ))
            return None
        if len(source_chains) > 1:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_SOURCE_AMBIGUOUS",
                f"按源报文名、CAN ID 0x{key.source_can_id:X}、通道“{key.source_channel}”"
                f"找到 {len(source_chains)} 条 PduRSrcPdu 源链：{sorted(source_chains)}。",
            ))
            return None
        if source_chains:
            # 先锁定源 RoutingPath，再用其 DestPdu 的 EcuC 引用过滤目标；同名同 ID 的本地自发
            # CanIfTxPdu 若不属于这条路径，不能成为 DELETE 候选，也不应制造虚假歧义。
            path_node = next(iter(source_chains.values()))[2]
            routed_target_pdus = {
                target
                for destination in _subcontainers(path_node, self.document.namespace, defs.PDUR_DEST)
                for target in semantic_values(destination, self.document.namespace)[1].get(
                    defs.PDUR_DEST_PDU_REF, (),
                )
            }
            target_chains = {
                identity: chain for identity, chain in target_chains.items()
                if identity[1] in routed_target_pdus
            }
            target_semantic_candidates = [
                identity for identity in target_semantic_candidates
                if identity[1] in routed_target_pdus
            ]
        if target_semantic_candidates and not target_chains:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_TARGET_SEMANTIC_CONFLICT",
                f"该源 RoutingPath 下，目标 CAN ID 0x{key.target_can_id:X} 与 TxBuffer"
                f"“{buffer_path}”命中对象 {target_semantic_candidates}，但报文名"
                f"“{key.target_message_name}”不匹配；不能按幂等 DELETE 跳过。",
            ))
            return None
        if len(target_chains) > 1:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_TARGET_AMBIGUOUS",
                f"按目标报文名、CAN ID 0x{key.target_can_id:X}、通道“{key.target_channel}”"
                f"找到 {len(target_chains)} 个目标 CanIf/EcuC 身份：{sorted(target_chains)}。",
            ))
            return None
        if not source_chains or not target_chains:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_NOT_FOUND",
                f"语义定位得到源链 {len(source_chains)} 条、目标身份 {len(target_chains)} 个；"
                "相关路由腿已不存在，本条 DELETE 幂等跳过。",
                warning=True,
            ))
            return None

        (source_canif_path, source_pdur_path, path_path), (
            source_canif, source_node, path_node,
        ) = next(iter(source_chains.items()))
        (target_canif_path, target_ecuc_path), (target_canif, _) = next(iter(target_chains.items()))
        source_ecuc_path = semantic_values(source_canif, self.document.namespace)[1][defs.CANIF_RX_PDU_REF][0]
        destinations = tuple(
            node for node in _subcontainers(path_node, self.document.namespace, defs.PDUR_DEST)
            if _message_identity_matches(
                direct_child_text(node, self.document.namespace, "SHORT-NAME"),
                key.target_message_name,
            ) and _semantics_match(
                node, self.document.namespace,
                {defs.PDUR_DEST_DIRECTION: "TRANSMIT"},
                {defs.PDUR_DEST_PDU_REF: target_ecuc_path},
            )
        )
        if not destinations:
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_NOT_FOUND",
                f"语义定位到 RoutingPath“{path_path}”，但其中不存在目标报文“"
                f"{key.target_message_name}”/0x{key.target_can_id:X}/{key.target_channel} 的目标腿，"
                "本条 DELETE 幂等跳过。",
                warning=True,
            ))
            return None
        if len(destinations) != 1:
            paths = sorted(autosar_path(node, self.document.namespace) for node in destinations)
            self.issues.append(_issue(
                self.workbook, route, "DIRECT_DELETE_DESTINATION_AMBIGUOUS",
                f"RoutingPath“{path_path}”内找到 {len(destinations)} 个目标腿候选：{paths}。",
            ))
            return None
        destination = destinations[0]
        return _DirectMatch(
            route, path_path, autosar_path(destination, self.document.namespace),
            source_pdur_path,
            source_ecuc_path, source_canif_path, target_ecuc_path, target_canif_path,
        )

    def _plan_direct(self) -> tuple[int, int]:
        routes = sorted(
            (route for route in self.workbook.direct_routes if route.operation is OperationType.DELETE),
            key=lambda route: (
                route.key.source_channel, route.key.source_message_name, route.key.source_can_id,
                route.key.target_channel, route.key.target_message_name, route.key.target_can_id,
            ),
        )
        matches: list[_DirectMatch] = []
        before_errors = len([issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR])
        for route in routes:
            match = self._match_direct(route)
            if match is not None:
                matches.append(match)
        missing = sum(issue.code == "DIRECT_DELETE_NOT_FOUND" for issue in self.issues)
        if len([issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR]) > before_errors:
            return len(matches), missing

        if not matches and routes:
            _, index_problems = self.routing_groups.application_group_mapping(routes[0].source)
            for problem in index_problems:
                self.issues.append(_membership_issue(
                    self.workbook, routes[0], problem, self.document.source_path,
                ))
            return len(matches), missing

        requests: list[RoutingGroupMembershipRequest] = []
        for match in matches:
            buffer_path = self._channel_path(match.route, source=False)
            if buffer_path is not None:
                requests.append(RoutingGroupMembershipRequest(
                    match.route.key.target_channel,
                    buffer_path,
                    match.destination_path,
                    match.route.source,
                ))
        membership = self.routing_groups.plan_delete(tuple(requests))
        routes_by_source = {route.source: route for route in routes}
        for problem in membership.problems:
            route = routes[0] if problem.warning else routes_by_source[problem.source]
            self.issues.append(_membership_issue(
                self.workbook, route, problem, self.document.source_path,
            ))
        if any(not problem.warning for problem in membership.problems):
            return len(matches), missing
        removed_group_referrers = self.routing_groups.removal_referrers(membership.operations)

        removed_paths = {match.destination_path for match in matches}
        for match in matches:
            destination = self.index.find_by_path(match.destination_path)[0]
            external_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, destination, removed_paths,
                removed_referrers=removed_group_referrers,
            )
            if external_users:
                self.issues.append(_issue(
                    self.workbook, match.route, "DIRECT_DELETE_DESTINATION_REFERENCED",
                    f"PduR 目标腿“{match.destination_path}”或其子对象仍被 "
                    f"{len(external_users)} 个外部对象引用。为避免产生悬空引用，"
                    "本次阻止全部输出；请先在 DaVinci 中解除或确认该引用。",
                ))
        if any(issue.severity is ValidationSeverity.ERROR for issue in self.issues):
            return len(matches), missing

        for operation in membership.operations:
            self._add_operation(operation)

        by_path: dict[str, list[_DirectMatch]] = defaultdict(list)
        for match in matches:
            by_path[match.path_path].append(match)
            node = self.index.find_by_path(match.destination_path)[0]
            self._add_operation(_node_operation(
                node, self.document.namespace, MutationKind.PDUR_DEST_PDU,
                MutationAction.REMOVE, (match.route.source,),
            ))
        for path_path, path_matches in sorted(by_path.items()):
            path_node = self.index.find_by_path(path_path)[0]
            all_destinations = _subcontainers(path_node, self.document.namespace, defs.PDUR_DEST)
            remaining = tuple(node for node in all_destinations
                              if autosar_path(node, self.document.namespace) not in removed_paths)
            locations = tuple(match.route.source for match in path_matches)
            source_path = path_matches[0].source_pdur_path
            source_node = self.index.find_by_path(source_path)[0]
            group = path_node.find(qualified(self.document.namespace, "SUB-CONTAINERS"))
            unknown_children = tuple(
                child for child in (list(group) if group is not None else ())
                if definition_ref(child, self.document.namespace) not in {defs.PDUR_SRC, defs.PDUR_DEST}
            )
            removal_projection = removed_paths | {path_path, source_path}
            source_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, source_node, removal_projection,
            )
            path_users = _remaining_referrers(
                self.index, self.document.namespace, path_path, removal_projection,
            )
            if remaining or unknown_children or source_users or path_users:
                if remaining:
                    reason = (
                        f"本次仅删除指定目标报文；删除后同一源报文仍路由到另外 {len(remaining)} 个"
                        "目标报文，因此保留 RoutingPath 和源端完整链。"
                    )
                elif unknown_children:
                    reason = (f"RoutingPath 内仍有 {len(unknown_children)} 个非标准人工子容器，"
                              "仅删除目标腿并保留父路径和源端链。")
                else:
                    reason = (f"RoutingPath 或 SrcPdu 在删除投影中仍被 {len(path_users) + len(source_users)} "
                              "个对象引用，保留父路径和源端链以避免悬空引用。")
                self._retain(path_path, reason, "DIRECT_SHARED_SOURCE", locations)
                self._add_operation(_node_operation(
                    path_node, self.document.namespace, MutationKind.PDUR_ROUTING_PATH,
                    MutationAction.RETAIN, locations, preserve_semantics=True,
                ))
                self._add_operation(_node_operation(
                    source_node, self.document.namespace, MutationKind.PDUR_SRC_PDU,
                    MutationAction.RETAIN, locations, preserve_semantics=True,
                ))
                source_canif_path = path_matches[0].source_canif_path
                source_ecuc_path = path_matches[0].source_ecuc_path
                source_canif = self.index.find_by_path(source_canif_path)[0]
                source_ecuc = self.index.find_by_path(source_ecuc_path)[0]
                self._retain(
                    source_canif_path, reason, "DIRECT_SHARED_SOURCE", locations,
                )
                self._retain(
                    source_ecuc_path, reason, "DIRECT_SHARED_SOURCE", locations,
                )
                self._add_operation(_node_operation(
                    source_canif, self.document.namespace, MutationKind.CANIF_RX_PDU,
                    MutationAction.RETAIN, locations, preserve_semantics=True,
                ))
                self._add_operation(_node_operation(
                    source_ecuc, self.document.namespace, MutationKind.ECUC_PDU,
                    MutationAction.RETAIN, locations, preserve_semantics=True,
                ))
                for node in unknown_children:
                    self._retain(
                        autosar_path(node, self.document.namespace), reason,
                        "DIRECT_MANUAL_CHILD", locations,
                    )
                for node in remaining:
                    self._add_operation(_node_operation(
                        node, self.document.namespace, MutationKind.PDUR_DEST_PDU,
                        MutationAction.RETAIN, locations, preserve_semantics=True,
                    ))
                    _, references = semantic_values(node, self.document.namespace)
                    for target_ecuc_path in references.get(defs.PDUR_DEST_PDU_REF, ()):
                        target_ecuc_nodes = self.index.find_by_path(target_ecuc_path)
                        if len(target_ecuc_nodes) != 1:
                            continue
                        self._retain(
                            target_ecuc_path, reason, "DIRECT_SHARED_TARGET", locations,
                        )
                        self._add_operation(_node_operation(
                            target_ecuc_nodes[0], self.document.namespace, MutationKind.ECUC_PDU,
                            MutationAction.RETAIN, locations, preserve_semantics=True,
                        ))
                        target_canif_nodes: dict[str, etree._Element] = {}
                        for referrer in self.index.find_referrers(target_ecuc_path):
                            owner = _container_owner(referrer)
                            if (owner is not None and definition_ref(owner, self.document.namespace)
                                    == defs.CANIF_TX):
                                target_canif_nodes[
                                    autosar_path(owner, self.document.namespace)
                                ] = owner
                        for target_canif_path, target_canif in sorted(target_canif_nodes.items()):
                            self._retain(
                                target_canif_path, reason, "DIRECT_SHARED_TARGET", locations,
                            )
                            self._add_operation(_node_operation(
                                target_canif, self.document.namespace, MutationKind.CANIF_TX_PDU,
                                MutationAction.RETAIN, locations, preserve_semantics=True,
                            ))
            else:
                # 父路径删除会连带删除 SrcPdu；将 SrcPdu 单独纳入计划，才能在
                # 输出验证中检查其路径和外部引用，而不是只验证父路径消失。
                removed_paths.add(source_path)
                self._add_operation(_node_operation(
                    source_node, self.document.namespace, MutationKind.PDUR_SRC_PDU,
                    MutationAction.REMOVE, locations,
                ))
                removed_paths.add(path_path)
                self._add_operation(_node_operation(
                    path_node, self.document.namespace, MutationKind.PDUR_ROUTING_PATH,
                    MutationAction.REMOVE, locations,
                ))

        canif_candidates: dict[str, tuple[MutationKind, tuple[SourceLocation, ...], str]] = {}
        ecuc_candidates: dict[str, tuple[MutationKind, tuple[SourceLocation, ...]]] = {}
        for match in matches:
            location = (match.route.source,)
            canif_candidates[match.target_canif_path] = (
                MutationKind.CANIF_TX_PDU, location, match.target_ecuc_path,
            )
            ecuc_candidates[match.target_ecuc_path] = (MutationKind.ECUC_PDU, location)
            if match.path_path in removed_paths:
                canif_candidates[match.source_canif_path] = (
                    MutationKind.CANIF_RX_PDU, location, match.source_ecuc_path,
                )
                ecuc_candidates[match.source_ecuc_path] = (MutationKind.ECUC_PDU, location)
        for canif_path, (kind, locations, ecuc_path) in sorted(canif_candidates.items()):
            pdur_definition = defs.PDUR_DEST if kind is MutationKind.CANIF_TX_PDU else defs.PDUR_SRC
            pdur_users = _remaining_referrers(
                self.index, self.document.namespace, ecuc_path, removed_paths,
                expected_definition=pdur_definition,
            )
            node = self.index.find_by_path(canif_path)[0]
            subtree_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, node, removed_paths | {canif_path},
            )
            manual_children = _direct_subcontainers(node, self.document.namespace)
            if pdur_users or subtree_users or manual_children:
                reason = (f"保留“{canif_path}”：删除投影中仍有 {len(pdur_users)} 个 PduR 引用和"
                          f" {len(subtree_users)} 个外部引用，"
                          f"并含有 {len(manual_children)} 个子容器。")
                self._retain(canif_path, reason, "DIRECT_SHARED_CANIF", locations)
                for child in manual_children:
                    self._retain(
                        autosar_path(child, self.document.namespace), reason,
                        "DIRECT_MANUAL_CHILD", locations,
                    )
                self._add_operation(_node_operation(
                    node, self.document.namespace, kind, MutationAction.RETAIN,
                    locations, preserve_semantics=True,
                ))
                continue
            node = self.index.find_by_path(canif_path)[0]
            self._add_operation(_node_operation(
                node, self.document.namespace, kind, MutationAction.REMOVE, locations,
            ))
            removed_paths.add(canif_path)
        for ecuc_path, (kind, locations) in sorted(ecuc_candidates.items()):
            node = self.index.find_by_path(ecuc_path)[0]
            subtree_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, node, removed_paths | {ecuc_path},
            )
            manual_children = _direct_subcontainers(node, self.document.namespace)
            if subtree_users or manual_children:
                reason = (f"保留“{ecuc_path}”：删除投影中仍被另外 "
                          f"{len(subtree_users)} 个对象引用，"
                          f"并含有 {len(manual_children)} 个子容器。")
                self._retain(ecuc_path, reason, "DIRECT_SHARED_ECUC", locations)
                for child in manual_children:
                    self._retain(
                        autosar_path(child, self.document.namespace), reason,
                        "DIRECT_MANUAL_CHILD", locations,
                    )
                self._add_operation(_node_operation(
                    node, self.document.namespace, kind, MutationAction.RETAIN,
                    locations, preserve_semantics=True,
                ))
                continue
            node = self.index.find_by_path(ecuc_path)[0]
            self._add_operation(_node_operation(
                node, self.document.namespace, kind, MutationAction.REMOVE, locations,
            ))
            removed_paths.add(ecuc_path)
        return len(matches), missing

    def _locate_signal_endpoint(
        self, route: SignalRouteChange, *, source: bool,
    ) -> etree._Element | None:
        key = route.key
        network = key.source_network if source else key.target_network
        message = key.source_message_name if source else key.target_message_name
        signal = key.source_signal_name if source else key.target_signal_name
        direction = "RECEIVE" if source else "TRANSMIT"
        role = "源" if source else "目标"
        state, ipdu, signal_node, candidate_count = self.com.locate_signal_endpoint(
            message, signal, network, direction,
        )
        if state in {"IPDU_MISSING", "IPDU_AMBIGUOUS"}:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_IPDU_UNRESOLVED",
                f"{role} ComIPdu（报文“{message}”、网段“{network}”、方向“{direction}”）"
                f"候选数为{candidate_count}，无法确认 DELETE 目标。请补齐 DBC 或清理重复候选。",
            ))
            return None
        if state != "FOUND" or signal_node is None:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_SIGNAL_UNRESOLVED",
                f"{role} ComSignal“{signal}”在报文“{message}”内候选数为"
                f"{candidate_count}，无法安全删除。请核对 DBC 和信号归属。",
            ))
            return None
        return signal_node

    def _match_signal(self, route: SignalRouteChange) -> _SignalMatch | None:
        source_signal = self._locate_signal_endpoint(route, source=True)
        target_signal = self._locate_signal_endpoint(route, source=False)
        if source_signal is None or target_signal is None:
            return None
        source_path = autosar_path(source_signal, self.document.namespace)
        target_path = autosar_path(target_signal, self.document.namespace)
        mappings = self.com.mappings_by_source(source_path)
        if not mappings:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_NOT_FOUND",
                f"源信号“{source_path}”的 ComGwMapping 已不存在，本条 DELETE 幂等跳过。",
                warning=True,
            ))
            return None
        matching: list[tuple[etree._Element, etree._Element, etree._Element]] = []
        ambiguous_destination = False
        ambiguous_source = False
        for candidate in mappings:
            sources = _subcontainers(candidate, self.document.namespace, defs.COM_GW_SOURCE)
            matching_sources = tuple(
                source for source in sources
                if semantic_values(source, self.document.namespace, recursive=True)[1].get(
                    defs.COM_GW_SOURCE_SIGNAL_REF
                ) == (source_path,)
            )
            if len(sources) != 1 or len(matching_sources) != 1:
                ambiguous_source = True
                continue
            destination_state, destination = self.com.find_destination_by_signal(candidate, target_path)
            if destination_state == "FOUND" and destination is not None:
                matching.append((candidate, destination, matching_sources[0]))
            elif destination_state == "AMBIGUOUS":
                ambiguous_destination = True
        if ambiguous_source:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_SOURCE_AMBIGUOUS",
                f"源信号“{source_path}”所在 Mapping 的 ComGwSource 总数或直接引用不唯一。"
                "为避免删除人工维护的 Source，本次阻止输出。",
            ))
            return None
        if ambiguous_destination or len(matching) > 1:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_DESTINATION_AMBIGUOUS",
                f"源信号“{source_path}”到目标“{target_path}”存在多个 Mapping/Destination 候选。"
                "请清理重复对象后重试。",
            ))
            return None
        if not matching:
            self.issues.append(_issue(
                self.workbook, route, "SIGNAL_DELETE_NOT_FOUND",
                f"以源信号“{source_path}”为 Source 的 Mapping 中，目标“{target_path}”"
                "Destination 已不存在，本条 DELETE 幂等跳过。", warning=True,
            ))
            return None
        mapping, destination, source = matching[0]
        return _SignalMatch(
            route, autosar_path(mapping, self.document.namespace),
            autosar_path(destination, self.document.namespace),
            autosar_path(source, self.document.namespace), source_path, target_path,
        )

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None

    def _plan_timeout_removal(
        self, source_path: str, matches: list[_SignalMatch],
        add_source_paths: set[str], removed_mapping_paths: set[str],
    ) -> bool:
        locations = tuple(match.route.source for match in matches)
        if source_path in add_source_paths:
            self._retain(source_path, "本次 ADD 仍会使用同一源信号，保守保留源端超时。",
                         "SIGNAL_TIMEOUT", locations)
            return False
        other_mappings = tuple(
            mapping for mapping in self.com.mappings_by_source(source_path)
            if autosar_path(mapping, self.document.namespace) not in removed_mapping_paths
        )
        if other_mappings:
            self._retain(
                source_path,
                f"仍有另外 {len(other_mappings)} 个 Mapping 使用同一源信号，保守保留源端超时。",
                "SIGNAL_TIMEOUT", locations,
            )
            return False
        timeout_inputs = {(self._decimal_text(match.route.timeout_time),
                           self._decimal_text(match.route.timeout_value)) for match in matches}
        if len(timeout_inputs) != 1:
            self._retain(source_path, "多条 DELETE 的超时证据不一致，无法确认参数归属。",
                         "SIGNAL_TIMEOUT", locations)
            return False
        timeout, substitution = next(iter(timeout_inputs))
        source = self.index.find_by_path(source_path)[0]
        actual, _ = semantic_values(source, self.document.namespace)
        current_action = actual.get(defs.COM_TIMEOUT_ACTION, ())
        current_timeout = actual.get(defs.COM_TIMEOUT, ())
        current_substitution = actual.get(defs.COM_TIMEOUT_SUBSTITUTION, ())
        if timeout is None:
            self._retain(source_path, "DELETE 行未提供超时时间，无法证明现有超时由本工具管理。",
                         "SIGNAL_TIMEOUT", locations)
            return False
        if current_action != ("REPLACE",) or current_timeout != (timeout,):
            self._retain(source_path, "现有超时动作或时间与 DELETE 标准值不一致，保守保留。",
                         "SIGNAL_TIMEOUT", locations)
            return False
        if current_substitution and (substitution is None or current_substitution != (substitution,)):
            self._retain(source_path, "现有超时替代值缺少匹配证据，保守保留全部超时参数。",
                         "SIGNAL_TIMEOUT", locations)
            return False
        parameters = [(defs.COM_TIMEOUT_ACTION, "REPLACE"), (defs.COM_TIMEOUT, timeout)]
        if current_substitution:
            parameters.append((defs.COM_TIMEOUT_SUBSTITUTION, substitution or ""))
        self._add_operation(MutationOperation(
            MutationKind.COM_SIGNAL_TIMEOUT, source_path, "", defs.COM_SIGNAL,
            action=MutationAction.REMOVE_PARAMETERS, parameters=tuple(parameters),
            source_locations=locations,
        ))
        return True

    def _plan_signal(self) -> tuple[int, int, int, int]:
        routes = sorted(
            (route for route in self.workbook.signal_routes if route.operation is OperationType.DELETE),
            key=lambda route: (
                route.key.source_network, route.key.source_message_name, route.key.source_signal_name,
                route.key.target_network, route.key.target_message_name, route.key.target_signal_name,
            ),
        )
        add_source_paths: set[str] = set()
        for route in self.workbook.signal_routes:
            if route.operation is not OperationType.ADD:
                continue
            state, _, source_signal, _ = self.com.locate_signal_endpoint(
                route.key.source_message_name, route.key.source_signal_name,
                route.key.source_network, "RECEIVE",
            )
            if state == "FOUND" and source_signal is not None:
                add_source_paths.add(autosar_path(source_signal, self.document.namespace))
        matches: list[_SignalMatch] = []
        before_errors = len([issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR])
        for route in routes:
            match = self._match_signal(route)
            if match is not None:
                matches.append(match)
        missing = sum(issue.code == "SIGNAL_DELETE_NOT_FOUND" for issue in self.issues)
        if len([issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR]) > before_errors:
            return len(matches), missing, 0, 0

        removed_destinations = {match.destination_path for match in matches}
        for match in matches:
            destination = self.index.find_by_path(match.destination_path)[0]
            external_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, destination, removed_destinations,
            )
            if external_users:
                self.issues.append(_issue(
                    self.workbook, match.route, "SIGNAL_DELETE_DESTINATION_REFERENCED",
                    f"ComGwDestination“{match.destination_path}”或其子对象仍被 "
                    f"{len(external_users)} 个外部对象引用。为避免产生悬空引用，"
                    "本次阻止全部输出；请先在 DaVinci 中解除或确认该引用。",
                ))
        if any(issue.severity is ValidationSeverity.ERROR for issue in self.issues):
            return len(matches), missing, 0, 0

        by_mapping: dict[str, list[_SignalMatch]] = defaultdict(list)
        for match in matches:
            by_mapping[match.mapping_path].append(match)
            node = self.index.find_by_path(match.destination_path)[0]
            self._add_operation(_node_operation(
                node, self.document.namespace, MutationKind.COM_GW_DESTINATION,
                MutationAction.REMOVE, (match.route.source,),
            ))
        retained_timeout_sources: set[str] = set()
        removable_matches_by_source: dict[str, list[_SignalMatch]] = defaultdict(list)
        removed_mapping_paths: set[str] = set()
        for mapping_path, mapping_matches in sorted(by_mapping.items()):
            mapping = self.index.find_by_path(mapping_path)[0]
            destinations = _subcontainers(mapping, self.document.namespace, defs.COM_GW_DEST)
            remaining = tuple(node for node in destinations
                              if autosar_path(node, self.document.namespace) not in removed_destinations)
            locations = tuple(match.route.source for match in mapping_matches)
            source_path = mapping_matches[0].source_signal_path
            source_container_path = mapping_matches[0].source_container_path
            source_container = self.index.find_by_path(source_container_path)[0]
            group = mapping.find(qualified(self.document.namespace, "SUB-CONTAINERS"))
            unknown_children = tuple(
                child for child in (list(group) if group is not None else ())
                if definition_ref(child, self.document.namespace)
                not in {defs.COM_GW_SOURCE, defs.COM_GW_DEST}
            )
            removal_projection = removed_destinations | {mapping_path, source_container_path}
            source_users = _remaining_subtree_referrers(
                self.index, self.document.namespace, source_container,
                removal_projection,
            )
            mapping_users = _remaining_referrers(
                self.index, self.document.namespace, mapping_path, removal_projection,
            )
            if remaining or unknown_children or source_users or mapping_users:
                if remaining:
                    reason = (
                        f"本次仅删除指定目标信号；删除后该源信号仍路由到另外 {len(remaining)} 个"
                        "目标信号，因此保留 Mapping、Source 和源端超时。"
                    )
                elif unknown_children:
                    reason = (f"Mapping 内仍有 {len(unknown_children)} 个非标准人工子容器，"
                              "仅删除目标并保留 Mapping、Source 和超时。")
                else:
                    reason = (f"Mapping 或 Source 在删除投影中仍被 "
                              f"{len(mapping_users) + len(source_users)} 个对象引用，"
                              "仅删除目标并保留源链。")
                self._retain(mapping_path, reason, "SIGNAL_SHARED_MAPPING", locations)
                self._retain(source_path, reason, "SIGNAL_TIMEOUT", locations)
                retained_timeout_sources.add(source_path)
                self._add_operation(_node_operation(
                    mapping, self.document.namespace, MutationKind.COM_GW_MAPPING,
                    MutationAction.RETAIN, locations,
                ))
                self._add_operation(_node_operation(
                    source_container, self.document.namespace, MutationKind.COM_GW_SOURCE,
                    MutationAction.RETAIN, locations, preserve_semantics=True,
                ))
                for node in unknown_children:
                    self._retain(
                        autosar_path(node, self.document.namespace), reason,
                        "SIGNAL_SHARED_MANUAL_CHILD", locations,
                    )
                for node in remaining:
                    self._add_operation(_node_operation(
                        node, self.document.namespace, MutationKind.COM_GW_DESTINATION,
                        MutationAction.RETAIN, locations, preserve_semantics=True,
                    ))
                continue
            self._add_operation(_node_operation(
                source_container, self.document.namespace, MutationKind.COM_GW_SOURCE,
                MutationAction.REMOVE, locations,
            ))
            self._add_operation(_node_operation(
                mapping, self.document.namespace, MutationKind.COM_GW_MAPPING,
                MutationAction.REMOVE, locations,
            ))
            removed_mapping_paths.add(mapping_path)
            removable_matches_by_source[source_path].extend(mapping_matches)

        removed_timeout_sources: set[str] = set()
        for source_path, source_matches in sorted(removable_matches_by_source.items()):
            # 只要同源的任意 Mapping 因共享目标、人工子容器或外部引用被保留，
            # 源端超时就不能清理；否则一次性基于整批 Mapping 删除投影判断。
            if source_path in retained_timeout_sources:
                continue
            if self._plan_timeout_removal(
                source_path, source_matches, add_source_paths, removed_mapping_paths,
            ):
                removed_timeout_sources.add(source_path)
            else:
                retained_timeout_sources.add(source_path)
        return (
            len(matches), missing,
            len(removed_timeout_sources), len(retained_timeout_sources),
        )

    def plan(self) -> MutationPlan:
        """一次性完成所有 DELETE 的定位、共享引用投影和参数删除决策。"""
        direct_deleted, direct_missing = self._plan_direct()
        signal_deleted, signal_missing, timeout_removed, timeout_retained = self._plan_signal()
        errors = tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)
        operations = tuple(sorted(
            self.operations.values(),
            key=lambda operation: (
                0 if operation.action is MutationAction.REMOVE_REFERENCE else
                1 if operation.action is MutationAction.REMOVE else
                2 if operation.action is MutationAction.REMOVE_PARAMETERS else 3,
                -operation.object_path.count("/"), operation.object_path,
                operation.references,
            ),
        ))
        return MutationPlan(
            operations=operations, issues=tuple(self.issues),
            direct_deleted_count=direct_deleted, direct_missing_count=direct_missing,
            direct_retained_count=sum(decision.category.startswith("DIRECT")
                                      for decision in self.decisions.values()),
            direct_conflict_count=sum(issue.code.startswith("DIRECT_DELETE") for issue in errors),
            signal_deleted_count=signal_deleted, signal_missing_count=signal_missing,
            signal_retained_count=sum(decision.category.startswith("SIGNAL_SHARED")
                                      for decision in self.decisions.values()),
            signal_timeout_removed_count=timeout_removed,
            signal_timeout_retained_count=timeout_retained,
            signal_conflict_count=sum(issue.code.startswith("SIGNAL_DELETE") for issue in errors),
            decisions=tuple(self.decisions.values()),
        )

    def apply(self, plan: MutationPlan) -> ArxmlIndex:
        """应用删除并返回投影索引；仅在树实际变化时于阶段边界重建。"""
        if plan.errors:
            raise ArxmlStructureError("DELETE 计划包含阻断错误，禁止修改工作副本。")
        mutated = False
        for operation in plan.operations:
            if operation.action is MutationAction.RETAIN:
                continue
            if operation.action is MutationAction.REMOVE_REFERENCE:
                self.routing_groups.apply_remove(operation)
                mutated = True
            elif operation.action is MutationAction.REMOVE:
                found = self.index.find_by_path(operation.object_path)
                if len(found) != 1:
                    raise ArxmlStructureError(
                        f"应用 DELETE 时对象“{operation.object_path}”实际找到{len(found)}个。"
                    )
                parent = found[0].getparent()
                if parent is None:
                    raise ArxmlStructureError(f"对象“{operation.object_path}”没有可删除父节点。")
                parent.remove(found[0])
                mutated = True
            elif operation.action is MutationAction.REMOVE_PARAMETERS:
                found = self.index.find_by_path(operation.object_path)
                if len(found) != 1:
                    raise ArxmlStructureError(
                        f"删除超时参数时源信号“{operation.object_path}”实际找到{len(found)}个。"
                    )
                definitions = {definition for definition, _ in operation.parameters}
                parameters = found[0].find(qualified(self.document.namespace, "PARAMETER-VALUES"))
                if parameters is None:
                    raise ArxmlStructureError(f"源信号“{operation.object_path}”缺少 PARAMETER-VALUES。")
                for entry in list(parameters):
                    if definition_ref(entry, self.document.namespace) in definitions:
                        parameters.remove(entry)
                        mutated = True
        # 删除会使路径和反向引用全部失效；统一在阶段边界重建，后续 ADD 不读取旧索引。
        return self.document.build_index() if mutated else self.index
