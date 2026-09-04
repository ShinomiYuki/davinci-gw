"""直接报文 ADD 分组与完整预检；一个源对象复用到多个目标路由腿。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from davinci_gw.arxml.index import autosar_path
from davinci_gw.domain.models import (
    DirectRouteChange,
    MutationOperation,
    SourceLocation,
    ValidationIssue,
    ValidationSeverity,
    WorkbookData,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.canif_editor import CanIfEditor
from davinci_gw.modules.common import unique_named_node
from davinci_gw.modules.ecuc_editor import EcucEditor
from davinci_gw.modules.pdur_editor import PduREditor

from .naming import direct_source_name, direct_target_name, pdur_leg_name, pdur_path_name
from .direct_route_locator import DirectRouteSemanticLocator, DirectSemanticState
from .routing_group_membership import (
    RoutingGroupMembershipProblem,
    RoutingGroupMembershipRequest,
    RoutingGroupMembershipService,
)

SUPPORTED_CAN_TYPES = {"STANDARD_CAN", "STANDARD_FD_CAN", "EXTENDED_CAN", "EXTENDED_FD_CAN"}
SUPPORTED_LENGTH_STRATEGIES = {"IGNORE", "SHORTEN", "DISCARD"}
SUPPORTED_SWITCHES = {"ENABLE", "DISABLE"}


@dataclass(frozen=True, slots=True)
class DirectPlanningResult:
    """直接报文规划阶段的操作、问题和路由腿统计。"""

    operations: tuple[MutationOperation, ...]
    issues: tuple[ValidationIssue, ...]
    added: int
    existing: int
    skipped: int


def _locations(routes: list[DirectRouteChange]) -> tuple[SourceLocation, ...]:
    return tuple(route.source for route in routes)


def _issue(
    workbook: WorkbookData, route: DirectRouteChange, code: str, detail: str, *, warning: bool,
) -> ValidationIssue:
    location = route.source
    row = f"第{location.row_number}行" if location.row_number else ""
    key = route.key
    identity = (
        f"{key.source_message_name}/0x{key.source_can_id:X}/{key.source_channel} → "
        f"{key.target_message_name}/0x{key.target_can_id:X}/{key.target_channel}"
    )
    return ValidationIssue(
        code=code,
        message=(f"配置表“{workbook.path}”的“{location.sheet_name}”工作表{row}，"
                 f"路由“{identity}”：{detail}"),
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
    """基线问题归属 ARXML；只有请求级问题才使用 Excel 行身份。"""
    if problem.baseline:
        return ValidationIssue(
            code=problem.code,
            message=problem.message,
            severity=(
                ValidationSeverity.WARNING if problem.warning else ValidationSeverity.ERROR
            ),
            file_path=baseline_path,
            location=SourceLocation(),
        )
    return _issue(workbook, route, problem.code, problem.message, warning=False)


def _is_disabled_or_blank(value: str | None) -> bool:
    return value is None or value.strip().upper() in {"", "DISABLE"}


def _deduplicate_issues(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    """基线问题不随需求行复制；请求级问题仍保留各自行号。"""
    unique: dict[tuple[object, ...], ValidationIssue] = {}
    for issue in issues:
        key = (issue.code, issue.message, issue.severity, issue.file_path, issue.location)
        unique.setdefault(key, issue)
    return tuple(unique.values())


class DirectRoutePlanner:
    """协调三个模块编辑器完成直接报文预检，但不修改 XML 树。"""

    def __init__(
        self,
        workbook: WorkbookData,
        ecuc: EcucEditor,
        canif: CanIfEditor,
        pdur: PduREditor,
        routing_groups: RoutingGroupMembershipService,
    ) -> None:
        self.workbook = workbook
        self.ecuc = ecuc
        self.canif = canif
        self.pdur = pdur
        self.routing_groups = routing_groups
        self.references = {entry.channel_name: entry for entry in workbook.reference_data}
        self.locator = DirectRouteSemanticLocator(
            canif.document,
            canif.index,
            routing_groups,
            source_module_ref=pdur.src_module_ref,
            destination_module_ref=pdur.dest_module_ref,
            lock_ref=pdur.lock_ref,
        )

    def _validate_source(self, routes: list[DirectRouteChange]) -> ValidationIssue | None:
        route = routes[0]
        shared = {(item.source_length, item.source_message_type, item.source_rx_indication_ul,
                   item.source_checksum_enabled, item.source_dlc_check_enabled) for item in routes}
        if len(shared) != 1:
            return _issue(
                self.workbook, route, "DIRECT_SOURCE_CONFLICT",
                "同一源报文的一对多路由填写了不同的源端参数。请统一Length、类型和源端策略后重试。",
                warning=False,
            )
        if route.source_message_type not in SUPPORTED_CAN_TYPES:
            return _issue(
                self.workbook, route, "DIRECT_SOURCE_TYPE_SKIPPED",
                f"源报文类型“{route.source_message_type}”当前无法安全映射，已跳过该源的全部ADD；"
                "请改为支持的CAN类型或先在DaVinci中补充同类型模板。",
                warning=True,
            )
        if (route.source_dlc_check_enabled or "").upper() not in SUPPORTED_SWITCHES:
            return _issue(
                self.workbook, route, "DIRECT_SOURCE_SWITCH_SKIPPED",
                f"源端Dlc Check值“{route.source_dlc_check_enabled}”不支持，已跳过该源的全部ADD；"
                "请使用Enable或Disable。", warning=True,
            )
        if not _is_disabled_or_blank(route.source_checksum_enabled):
            return _issue(
                self.workbook, route, "DIRECT_CHECKSUM_SKIPPED",
                "源端Checksum使能在当前真实CanIf模板中没有可安全映射的参数，已跳过该源的全部ADD；"
                "请先确认DaVinci项目的Checksum配置方式。", warning=True,
            )
        if (route.source_rx_indication_ul or "").upper() != "PDUR":
            return _issue(
                self.workbook, route, "DIRECT_RX_UL_SKIPPED",
                f"源端RxIndicationUL“{route.source_rx_indication_ul}”不是PDUR，已跳过该源的全部ADD。",
                warning=True,
            )
        return None

    def _validate_target(self, route: DirectRouteChange) -> ValidationIssue | None:
        if route.target_message_type not in SUPPORTED_CAN_TYPES:
            return _issue(
                self.workbook, route, "DIRECT_TARGET_TYPE_SKIPPED",
                f"目标报文类型“{route.target_message_type}”当前无法安全映射，已跳过该路由腿。",
                warning=True,
            )
        if (route.target_truncation_enabled or "").upper() not in SUPPORTED_SWITCHES:
            return _issue(
                self.workbook, route, "DIRECT_TARGET_SWITCH_SKIPPED",
                f"目标端Truncation值“{route.target_truncation_enabled}”不支持，已跳过该路由腿；"
                "请使用Enable或Disable。", warning=True,
            )
        strategy = (route.length_strategy or "").upper()
        if strategy not in SUPPORTED_LENGTH_STRATEGIES:
            return _issue(
                self.workbook, route, "DIRECT_LENGTH_STRATEGY_SKIPPED",
                f"Length Strategy“{route.length_strategy}”无法映射，已跳过该路由腿。",
                warning=True,
            )
        if not _is_disabled_or_blank(route.target_checksum_enabled) or not _is_disabled_or_blank(route.target_pn_filter_enabled):
            return _issue(
                self.workbook, route, "DIRECT_TARGET_POLICY_SKIPPED",
                "目标端Checksum或PnFilter在当前真实模板中没有可安全映射的参数，已跳过该路由腿。",
                warning=True,
            )
        return None

    def _channel_object(
        self, route: DirectRouteChange, *, source: bool,
    ) -> tuple[str | None, ValidationIssue | None]:
        channel = route.key.source_channel if source else route.key.target_channel
        entry = self.references.get(channel)
        role = "源" if source else "目标"
        field = "CanIfHrh名称" if source else "CanIfTxBuffer名称"
        expected_definition = defs.CANIF_HRH if source else defs.CANIF_BUFFER
        if entry is None:
            return None, _issue(
                self.workbook, route, "DIRECT_CHANNEL_SKIPPED",
                f"{role}CAN通道“{channel}”未在“引用数据”中定义，已跳过该路由腿；请补充通道映射。",
                warning=True,
            )
        name = entry.hrh_name if source else entry.tx_buffer_name
        if not name:
            return None, _issue(
                self.workbook, route, "DIRECT_CHANNEL_REFERENCE_SKIPPED",
                f"{role}CAN通道“{channel}”缺少“{field}”，已跳过该路由腿；请在引用数据中补充。",
                warning=True,
            )
        state, node = unique_named_node(self.canif.index, self.canif.document.namespace, name, expected_definition)
        if state == "MISSING":
            return None, _issue(
                self.workbook, route, "DIRECT_ARXML_REFERENCE_SKIPPED",
                f"基准ARXML中找不到{role}通道引用对象“{name}”，可能尚未导入对应DBC，已跳过该路由腿；"
                "请先更新DBC或核对引用数据。", warning=True,
            )
        if state == "AMBIGUOUS":
            return None, _issue(
                self.workbook, route, "DIRECT_ARXML_REFERENCE_AMBIGUOUS",
                f"基准ARXML中“{name}”存在多个{role}通道候选，无法安全选择。请在DaVinci中消除重名后重试。",
                warning=False,
            )
        return autosar_path(node, self.canif.document.namespace), None

    def plan(self, routes: tuple[DirectRouteChange, ...]) -> DirectPlanningResult:
        """按源身份稳定分组并对所有 ADD 完成预检，不修改 XML。"""
        groups: dict[tuple[str, int, str], list[DirectRouteChange]] = defaultdict(list)
        for route in routes:
            groups[(route.key.source_message_name, route.key.source_can_id, route.key.source_channel)].append(route)
        operations: list[MutationOperation] = []
        issues: list[ValidationIssue] = []
        added = existing = skipped = 0

        for group_key in sorted(groups):
            group = sorted(groups[group_key], key=lambda item: (
                item.key.target_channel, item.key.target_message_name, item.key.target_can_id,
            ))
            route = group[0]
            source_issue = self._validate_source(group)
            if source_issue:
                issues.append(source_issue)
                skipped += len(group)
                continue
            hrh_path, channel_issue = self._channel_object(route, source=True)
            if channel_issue:
                issues.append(channel_issue)
                skipped += len(group)
                continue

            prepared_targets: list[tuple[DirectRouteChange, str]] = []
            for target in group:
                target_issue = self._validate_target(target)
                if target_issue:
                    issues.append(target_issue)
                    skipped += 1
                    continue
                buffer_path, target_channel_issue = self._channel_object(target, source=False)
                if target_channel_issue:
                    issues.append(target_channel_issue)
                    skipped += 1
                    continue
                prepared_targets.append((target, buffer_path or ""))
            if not prepared_targets:
                continue

            locations = _locations(group)
            first_target, first_buffer = prepared_targets[0]
            first_semantic = self.locator.locate(
                first_target, hrh_path=hrh_path or "", buffer_path=first_buffer,
            )
            source_resolution = first_semantic.source
            if source_resolution.state in {
                DirectSemanticState.PARTIAL_CONFLICT, DirectSemanticState.AMBIGUOUS,
            }:
                issues.append(_issue(
                    self.workbook, route,
                    "DIRECT_SOURCE_CHAIN_AMBIGUOUS"
                    if source_resolution.state is DirectSemanticState.AMBIGUOUS
                    else "DIRECT_SOURCE_CHAIN_CONFLICT",
                    f"源路由语义定位状态为 {source_resolution.state.value}；"
                    f"候选={list(source_resolution.candidates)}；{source_resolution.detail}",
                    warning=False,
                ))
                skipped += len(prepared_targets)
                continue

            source_operations: list[MutationOperation] = []
            if source_resolution.state is DirectSemanticState.FOUND:
                assert source_resolution.chain is not None
                source_path = source_resolution.chain.routing_path
            else:
                source_name = direct_source_name(route.key.source_message_name, route.key.source_channel)
                path_name = pdur_path_name(
                    route.key.source_message_name, route.key.source_can_id, route.key.source_channel,
                )
                src_name = pdur_leg_name(
                    route.key.source_message_name, route.key.source_can_id, route.key.source_channel,
                )
                ecuc_source = self.ecuc.pdu_operation(
                    source_name, route.source_length or 0, locations,
                )
                rx_probe = self.canif.rx_operation(
                    route, source_name, ecuc_source.object_path, hrh_path or "", locations,
                    allocate_handle=False,
                )
                pdur_path = self.pdur.path_operation(path_name, locations)
                pdur_source_probe = self.pdur.source_operation(
                    pdur_path.object_path, src_name, ecuc_source.object_path, locations,
                    allocate_handle=False,
                )
                source_states = (
                    self.ecuc.inspect(ecuc_source), self.canif.inspect(rx_probe),
                    self.pdur.inspect(pdur_path), self.pdur.inspect(pdur_source_probe),
                )
                if not all(state == "MISSING" for state in source_states):
                    issues.append(_issue(
                        self.workbook, route, "DIRECT_SOURCE_CHAIN_CONFLICT",
                        f"语义定位未找到完整源链，但当前命名路径只存在部分对象或参数不同，"
                        f"状态为{source_states}。请在 DaVinci 中修复后重试。", warning=False,
                    ))
                    skipped += len(prepared_targets)
                    continue
                source_path = pdur_path.object_path
                source_operations.extend((
                    ecuc_source,
                    self.canif.rx_operation(
                        route, source_name, ecuc_source.object_path, hrh_path or "", locations,
                        allocate_handle=True,
                    ),
                    pdur_path,
                    self.pdur.source_operation(
                        pdur_path.object_path, src_name, ecuc_source.object_path, locations,
                        allocate_handle=True,
                    ),
                ))

            group_operations: list[MutationOperation] = []
            group_added = 0
            for target, buffer_path in prepared_targets:
                semantic = self.locator.locate(
                    target, hrh_path=hrh_path or "", buffer_path=buffer_path,
                )
                if semantic.leg.state in {
                    DirectSemanticState.PARTIAL_CONFLICT, DirectSemanticState.AMBIGUOUS,
                }:
                    issues.append(_issue(
                        self.workbook, target,
                        "DIRECT_ROUTE_LEG_AMBIGUOUS"
                        if semantic.leg.state is DirectSemanticState.AMBIGUOUS
                        else "DIRECT_ROUTE_LEG_CONFLICT",
                        f"目标腿语义定位状态为 {semantic.leg.state.value}；"
                        f"候选={list(semantic.leg.candidates)}；{semantic.leg.detail}",
                        warning=False,
                    ))
                    skipped += 1
                    continue
                if semantic.leg.state is DirectSemanticState.FOUND:
                    assert semantic.leg.leg is not None
                    membership = self.routing_groups.plan_add(RoutingGroupMembershipRequest(
                        target.key.target_channel, buffer_path,
                        semantic.leg.leg.destination_path, target.source,
                    ))
                    issues.extend(_membership_issue(
                        self.workbook, target, problem, self.routing_groups.document.source_path,
                    ) for problem in membership.problems)
                    if any(not problem.warning for problem in membership.problems):
                        skipped += 1
                        continue
                    group_operations.extend(membership.operations)
                    existing += 1
                    continue

                if semantic.target.state in {
                    DirectSemanticState.PARTIAL_CONFLICT, DirectSemanticState.AMBIGUOUS,
                }:
                    issues.append(_issue(
                        self.workbook, target,
                        "DIRECT_TARGET_ENDPOINT_AMBIGUOUS"
                        if semantic.target.state is DirectSemanticState.AMBIGUOUS
                        else "DIRECT_TARGET_ENDPOINT_CONFLICT",
                        f"目标端语义定位状态为 {semantic.target.state.value}；"
                        f"候选={list(semantic.target.candidates)}；{semantic.target.detail}",
                        warning=False,
                    ))
                    skipped += 1
                    continue

                target_name = direct_target_name(target.key.target_message_name, target.key.target_channel)
                dest_name = pdur_leg_name(
                    target.key.target_message_name, target.key.target_can_id, target.key.target_channel,
                )
                target_location = (target.source,)
                target_operations: list[MutationOperation] = []
                if semantic.target.state is DirectSemanticState.FOUND:
                    assert semantic.target.endpoint is not None
                    target_ecuc_path = semantic.target.endpoint.ecuc_path
                else:
                    ecuc_target = self.ecuc.pdu_operation(
                        target_name, target.target_length or 0, target_location,
                    )
                    tx_probe = self.canif.tx_operation(
                        target, target_name, ecuc_target.object_path, buffer_path, target_location,
                        allocate_handle=False,
                    )
                    endpoint_states = self.ecuc.inspect(ecuc_target), self.canif.inspect(tx_probe)
                    if not all(state == "MISSING" for state in endpoint_states):
                        issues.append(_issue(
                            self.workbook, target, "DIRECT_TARGET_ENDPOINT_CONFLICT",
                            f"语义定位未找到可复用目标端，但当前命名端点存在部分对象或参数不同，"
                            f"状态为{endpoint_states}。", warning=False,
                        ))
                        skipped += 1
                        continue
                    target_ecuc_path = ecuc_target.object_path
                    target_operations.extend((
                        ecuc_target,
                        self.canif.tx_operation(
                            target, target_name, target_ecuc_path, buffer_path, target_location,
                            allocate_handle=True,
                        ),
                    ))
                dest_probe = self.pdur.destination_operation(
                    source_path, dest_name, target_ecuc_path,
                    (target.length_strategy or "").upper(), target_location,
                    allocate_handle=False,
                )
                destination_state = self.pdur.inspect(dest_probe)
                if destination_state != "MISSING":
                    issues.append(_issue(
                        self.workbook, target, "DIRECT_ROUTE_LEG_CONFLICT",
                        f"语义定位未找到目标腿，但计划路径“{dest_probe.object_path}”状态为"
                        f"{destination_state}。请修复同名冲突后重试。", warning=False,
                    ))
                    skipped += 1
                    continue
                membership = self.routing_groups.plan_add(RoutingGroupMembershipRequest(
                    target.key.target_channel, buffer_path, dest_probe.object_path,
                    target.source, destination_exists=False,
                ))
                issues.extend(_membership_issue(
                    self.workbook, target, problem, self.routing_groups.document.source_path,
                ) for problem in membership.problems)
                if any(not problem.warning for problem in membership.problems):
                    skipped += 1
                    continue
                group_operations.extend(target_operations)
                group_operations.append(self.pdur.destination_operation(
                    source_path, dest_name, target_ecuc_path,
                    (target.length_strategy or "").upper(), target_location,
                    allocate_handle=True,
                ))
                group_operations.extend(membership.operations)
                group_added += 1
            if group_operations:
                operations.extend(source_operations)
                operations.extend(group_operations)
            if group_added:
                added += group_added
        return DirectPlanningResult(
            tuple(operations), _deduplicate_issues(issues), added, existing, skipped,
        )
