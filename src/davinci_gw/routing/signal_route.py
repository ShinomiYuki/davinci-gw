"""信号路由 ADD 分组、Com 对象精确定位、缺失跳过和一对多计划。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from lxml import etree

from davinci_gw.arxml.index import autosar_path
from davinci_gw.arxml.namespace import direct_child_text, qualified
from davinci_gw.domain.models import (
    MutationOperation,
    SignalRouteChange,
    SourceLocation,
    ValidationIssue,
    ValidationSeverity,
    WorkbookData,
)
from davinci_gw.modules import definitions as defs
from davinci_gw.modules.com_editor import ComEditor
from davinci_gw.modules.common import definition_ref, semantic_values

from .naming import com_destination_name, com_mapping_name, com_source_name


@dataclass(frozen=True, slots=True)
class SignalPlanningResult:
    """信号路由规划阶段的操作、问题和路由腿统计。"""

    operations: tuple[MutationOperation, ...]
    issues: tuple[ValidationIssue, ...]
    added: int
    existing: int
    skipped: int


def _issue(
    workbook: WorkbookData, route: SignalRouteChange, code: str, detail: str, *, warning: bool,
) -> ValidationIssue:
    location = route.source
    row = f"第{location.row_number}行" if location.row_number else ""
    return ValidationIssue(
        code=code,
        message=f"配置表“{workbook.path}”的“{location.sheet_name}”工作表{row}，{detail}",
        severity=ValidationSeverity.WARNING if warning else ValidationSeverity.ERROR,
        file_path=workbook.path,
        location=location,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


class SignalRoutePlanner:
    """使用 ComIPdu 的信号引用验证归属，再规划 Mapping、Destination 和源超时。"""

    def __init__(self, workbook: WorkbookData, com: ComEditor) -> None:
        self.workbook = workbook
        self.com = com

    def _locate_endpoint(
        self, route: SignalRouteChange, *, source: bool,
    ) -> tuple[etree._Element | None, etree._Element | None, ValidationIssue | None]:
        key = route.key
        network = key.source_network if source else key.target_network
        message = key.source_message_name if source else key.target_message_name
        signal = key.source_signal_name if source else key.target_signal_name
        role = "源" if source else "目标"
        direction = "RECEIVE" if source else "TRANSMIT"
        ipdu_state, ipdu = self.com.locate_ipdu(message, network, direction)
        if ipdu_state == "MISSING":
            return None, None, _issue(
                self.workbook, route, f"SIGNAL_{role}_IPDU_SKIPPED",
                f"基准ARXML中未找到{role}报文“{message}”在网段“{network}”的ComIPdu，"
                "可能尚未导入对应DBC，已跳过该信号路由；请更新DBC或核对报文名。",
                warning=True,
            )
        if ipdu_state == "AMBIGUOUS":
            return None, None, _issue(
                self.workbook, route, f"SIGNAL_{role}_IPDU_AMBIGUOUS",
                f"{role}报文“{message}”在网段“{network}”存在多个ComIPdu候选，无法安全选择。",
                warning=False,
            )
        signal_state, signal_node = self.com.locate_signal(ipdu, message, signal)
        if signal_state == "MISSING":
            return ipdu, None, _issue(
                self.workbook, route, f"SIGNAL_{role}_SIGNAL_SKIPPED",
                f"{role}ComIPdu“{direct_child_text(ipdu, self.com.document.namespace, 'SHORT-NAME')}”中"
                f"未找到信号“{signal}”，可能基线DBC版本较旧，已跳过该路由；请更新DBC后重试。",
                warning=True,
            )
        if signal_state == "AMBIGUOUS":
            return ipdu, None, _issue(
                self.workbook, route, f"SIGNAL_{role}_SIGNAL_AMBIGUOUS",
                f"{role}ComIPdu中信号“{signal}”存在多个候选，无法安全选择。",
                warning=False,
            )
        return ipdu, signal_node, None

    def _source_child(
        self, mapping: etree._Element, signal_path: str,
    ) -> tuple[str, etree._Element | None]:
        subcontainers = mapping.find(qualified(self.com.document.namespace, "SUB-CONTAINERS"))
        candidates: list[etree._Element] = []
        for child in list(subcontainers) if subcontainers is not None else []:
            if definition_ref(child, self.com.document.namespace) != defs.COM_GW_SOURCE:
                continue
            _, refs = semantic_values(child, self.com.document.namespace, recursive=True)
            if refs.get(defs.COM_GW_SOURCE_SIGNAL_REF) == (signal_path,):
                candidates.append(child)
        if not candidates:
            return "MISSING", None
        if len(candidates) != 1:
            return "AMBIGUOUS", None
        return "FOUND", candidates[0]

    def plan(self, routes: tuple[SignalRouteChange, ...]) -> SignalPlanningResult:
        """按完整源身份分组；单条缺失会跳过，冲突才阻断整次生成。"""
        groups: dict[tuple[str, str, str], list[SignalRouteChange]] = defaultdict(list)
        for route in routes:
            groups[(route.key.source_network, route.key.source_message_name, route.key.source_signal_name)].append(route)
        operations: list[MutationOperation] = []
        issues: list[ValidationIssue] = []
        added = existing = skipped = 0

        for group_key in sorted(groups):
            group = sorted(groups[group_key], key=lambda item: (
                item.key.target_network, item.key.target_message_name, item.key.target_signal_name,
            ))
            route = group[0]
            timeout_values = {(_decimal_text(item.timeout_time), _decimal_text(item.timeout_value)) for item in group}
            if len(timeout_values) != 1:
                issues.append(_issue(
                    self.workbook, route, "SIGNAL_TIMEOUT_CONFLICT",
                    "同一源信号的一对多路由填写了不同的超时时间或超时值，请统一后重试。",
                    warning=False,
                ))
                continue
            _, source_signal, source_issue = self._locate_endpoint(route, source=True)
            if source_issue:
                issues.append(source_issue)
                if source_issue.severity is ValidationSeverity.WARNING:
                    skipped += len(group)
                continue
            source_path = autosar_path(source_signal, self.com.document.namespace)

            valid_targets: list[tuple[SignalRouteChange, str]] = []
            for target in group:
                _, target_signal, target_issue = self._locate_endpoint(target, source=False)
                if target_issue:
                    issues.append(target_issue)
                    if target_issue.severity is ValidationSeverity.WARNING:
                        skipped += 1
                    continue
                valid_targets.append((target, autosar_path(target_signal, self.com.document.namespace)))
            if not valid_targets:
                continue

            locations = tuple(item.source for item, _ in valid_targets)
            expected_mapping = self.com.mapping_operation(
                com_mapping_name(route.key.source_signal_name, route.key.source_network), locations,
            )
            mapping_state, mapping_node = self.com.find_mapping_by_source(source_path)
            mapping_path: str
            mapping_new = False
            if mapping_state == "AMBIGUOUS":
                issues.append(_issue(
                    self.workbook, route, "SIGNAL_MAPPING_SOURCE_AMBIGUOUS",
                    f"源ComSignal“{source_path}”被多个ComGwMapping作为Source引用，无法确定更新目标。",
                    warning=False,
                ))
                continue
            if mapping_state == "FOUND":
                mapping_path = autosar_path(mapping_node, self.com.document.namespace)
                source_state, _ = self._source_child(mapping_node, source_path)
                if source_state != "FOUND":
                    issues.append(_issue(
                        self.workbook, route, "SIGNAL_MAPPING_SOURCE_CONFLICT",
                        f"已有Mapping“{mapping_path}”的Source结构不唯一或不完整，请在DaVinci中修复后重试。",
                        warning=False,
                    ))
                    continue
            else:
                source_probe = self.com.source_operation(
                    expected_mapping.object_path,
                    com_source_name(route.key.source_signal_name, route.key.source_network),
                    source_path, locations,
                )
                states = (self.com.inspect(expected_mapping), self.com.inspect(source_probe))
                if states == ("MISSING", "MISSING"):
                    mapping_new = True
                    mapping_path = expected_mapping.object_path
                    operations.extend((expected_mapping, source_probe))
                elif states == ("EXISTING", "EXISTING"):
                    mapping_path = expected_mapping.object_path
                    mapping_node = self.com.index.find_by_path(mapping_path)[0]
                else:
                    issues.append(_issue(
                        self.workbook, route, "SIGNAL_MAPPING_CONFLICT",
                        f"确定性Mapping“{expected_mapping.object_path}”存在部分结构或Source引用不同，"
                        f"状态为{states}。请修复冲突后重试。", warning=False,
                    ))
                    continue

            group_added = group_existing = 0
            for target, target_path in valid_targets:
                if not mapping_new and mapping_node is not None:
                    destination_state, _ = self.com.find_destination_by_signal(mapping_node, target_path)
                    if destination_state == "FOUND":
                        group_existing += 1
                        continue
                    if destination_state == "AMBIGUOUS":
                        issues.append(_issue(
                            self.workbook, target, "SIGNAL_DESTINATION_AMBIGUOUS",
                            f"Mapping“{mapping_path}”中多个Destination指向“{target_path}”，请清理重复对象。",
                            warning=False,
                        ))
                        continue
                destination = self.com.destination_operation(
                    mapping_path,
                    com_destination_name(target.key.target_signal_name, target.key.target_network),
                    target_path, (target.source,),
                )
                state = "MISSING" if mapping_new else self.com.inspect(destination)
                if state == "MISSING":
                    operations.append(destination)
                    group_added += 1
                elif state == "EXISTING":
                    group_existing += 1
                else:
                    issues.append(_issue(
                        self.workbook, target, "SIGNAL_DESTINATION_CONFLICT",
                        f"Destination“{destination.object_path}”已存在但指向或定义不同，请修复后重试。",
                        warning=False,
                    ))

            timeout, substitution = next(iter(timeout_values))
            timeout_operation = self.com.timeout_operation(source_path, timeout, substitution, locations)
            if timeout_operation is not None and (group_added or group_existing):
                timeout_state = self.com.inspect_timeout(timeout_operation)
                if timeout_state == "MISSING":
                    operations.append(timeout_operation)
                elif timeout_state == "CONFLICT":
                    issues.append(_issue(
                        self.workbook, route, "SIGNAL_TIMEOUT_EXISTING_CONFLICT",
                        f"源ComSignal“{source_path}”已有超时参数与标准输入不同或重复，请人工确认后重试。",
                        warning=False,
                    ))
            added += group_added
            existing += group_existing

        return SignalPlanningResult(tuple(operations), tuple(issues), added, existing, skipped)
