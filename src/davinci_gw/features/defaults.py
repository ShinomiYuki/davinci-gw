"""直接报文和信号路由的默认动态统计实现。"""

from __future__ import annotations

from dataclasses import dataclass

from davinci_gw.contracts import ChangeDetailDto, FeatureSummaryDto, MetricDto
from davinci_gw.domain.models import (
    DirectRouteChange,
    MutationAction,
    MutationKind,
    MutationPlan,
    OperationType,
    SignalRouteChange,
)

from .base import FeatureContext
from .registry import FeatureRegistry


def _status(context: FeatureContext) -> str:
    if any(issue.severity == "ERROR" for issue in context.issues):
        return "ERROR"
    if context.plan is None:
        return "UNAVAILABLE"
    if any(issue.severity == "WARNING" for issue in context.issues):
        return "WARNING"
    return "READY"


def _location(route: DirectRouteChange | SignalRouteChange) -> str:
    source = route.source
    return f"{source.sheet_name} 第 {source.row_number} 行"


def _is_changed_route(
    route: DirectRouteChange | SignalRouteChange,
    plan: MutationPlan | None,
    destination_kind: MutationKind,
) -> bool:
    """以目标腿操作判断该配置行是否形成了实际路由新增或删除。"""
    if plan is None:
        return False
    expected_action = (
        MutationAction.CREATE if route.operation is OperationType.ADD else MutationAction.REMOVE
    )
    return any(
        operation.kind is destination_kind
        and operation.action is expected_action
        and route.source in operation.source_locations
        for operation in plan.operations
    )


@dataclass(frozen=True, slots=True)
class DirectMessageFeature:
    """把既有直接报文计划映射为动态公共指标。"""
    feature_id: str = "direct_message"
    display_name: str = "直接报文路由"
    capabilities: tuple[str, ...] = ("ADD", "DELETE", "PREVIEW", "GENERATE")

    def summarize(self, context: FeatureContext) -> FeatureSummaryDto:
        """收集请求和实际计划统计，不暴露 MutationPlan。"""
        workbook, plan = context.workbook, context.plan
        routes = workbook.direct_routes if workbook else ()
        metrics = (
            MetricDto("requested_add", "请求新增", sum(r.operation is OperationType.ADD for r in routes)),
            MetricDto("requested_delete", "请求删除", sum(r.operation is OperationType.DELETE for r in routes)),
            MetricDto("added", "计划新增", plan.direct_added_count if plan else 0),
            MetricDto("deleted", "计划删除", plan.direct_deleted_count if plan else 0),
            MetricDto("existing", "已存在", plan.direct_existing_count if plan else 0),
            MetricDto("skipped", "跳过", plan.direct_skipped_count if plan else 0),
            MetricDto("missing", "未找到", plan.direct_missing_count if plan else 0),
            MetricDto("retained", "保守保留", plan.direct_retained_count if plan else 0),
            MetricDto("conflicts", "冲突", plan.direct_conflict_count if plan else 0),
        )
        details = tuple(ChangeDetailDto(
            route.operation.value,
            "报文",
            f"{route.key.source_message_name} · 0x{route.key.source_can_id:X} · {route.key.source_channel}",
            f"{route.key.target_message_name} · 0x{route.key.target_can_id:X} · {route.key.target_channel}",
            _location(route),
        ) for route in routes if _is_changed_route(route, plan, MutationKind.PDUR_DEST_PDU))
        return FeatureSummaryDto(
            self.feature_id, self.display_name, _status(context), metrics, details=details,
        )


@dataclass(frozen=True, slots=True)
class SignalRoutingFeature:
    """把既有信号与超时计划映射为动态公共指标。"""
    feature_id: str = "signal_route"
    display_name: str = "信号路由"
    capabilities: tuple[str, ...] = ("ADD", "DELETE", "TIMEOUT", "PREVIEW", "GENERATE")

    def summarize(self, context: FeatureContext) -> FeatureSummaryDto:
        """收集信号路由请求和实际计划统计。"""
        workbook, plan = context.workbook, context.plan
        routes = workbook.signal_routes if workbook else ()
        metrics = (
            MetricDto("requested_add", "请求新增", sum(r.operation is OperationType.ADD for r in routes)),
            MetricDto("requested_delete", "请求删除", sum(r.operation is OperationType.DELETE for r in routes)),
            MetricDto("added", "计划新增", plan.signal_added_count if plan else 0),
            MetricDto("deleted", "计划删除", plan.signal_deleted_count if plan else 0),
            MetricDto("existing", "已存在", plan.signal_existing_count if plan else 0),
            MetricDto("skipped", "跳过", plan.signal_skipped_count if plan else 0),
            MetricDto("missing", "未找到", plan.signal_missing_count if plan else 0),
            MetricDto("retained", "保守保留", plan.signal_retained_count if plan else 0),
            MetricDto("timeout_removed", "移除超时", plan.signal_timeout_removed_count if plan else 0),
            MetricDto(
                "timeout_retained", "保留超时",
                plan.signal_timeout_retained_count if plan else 0,
            ),
            MetricDto("conflicts", "冲突", plan.signal_conflict_count if plan else 0),
        )
        details = tuple(ChangeDetailDto(
            route.operation.value,
            "信号",
            f"{route.key.source_network} · {route.key.source_message_name} · {route.key.source_signal_name}",
            f"{route.key.target_network} · {route.key.target_message_name} · {route.key.target_signal_name}",
            _location(route),
        ) for route in routes if _is_changed_route(
            route, plan, MutationKind.COM_GW_DESTINATION,
        ))
        return FeatureSummaryDto(
            self.feature_id, self.display_name, _status(context), metrics, details=details,
        )


def default_feature_registry() -> FeatureRegistry:
    """为每个 Facade 创建独立默认注册表，避免全局测试污染。"""
    return FeatureRegistry((DirectMessageFeature(), SignalRoutingFeature(), DiagnosticRoutingFeature()))


@dataclass(frozen=True, slots=True)
class DiagnosticRoutingFeature:
    """通过公共统计契约向 GUI、CLI 和 MCP 展示 CAN 诊断需求。"""

    feature_id: str = "diagnostic_route"
    display_name: str = "CAN诊断路由"
    capabilities: tuple[str, ...] = ("ADD", "DELETE", "PREVIEW", "GENERATE")

    def summarize(self, context: FeatureContext) -> FeatureSummaryDto:
        routes = context.workbook.diagnostic_routes if context.workbook else ()
        plan = context.plan
        metrics = tuple(MetricDto(name, label, getattr(plan, f"diagnostic_{name}_count") if plan else 0)
                        for name, label in (("added", "新增"), ("existing", "已有"), ("deleted", "删除"),
                                            ("missing", "未找到"), ("skipped", "跳过")))
        details = tuple(ChangeDetailDto(route.operation.value, "CAN诊断",
            f"{route.request_name} · {route.request_endpoint.channel if route.request_endpoint else '独立CAN侧'}",
            f"{route.response_name} · {route.response_endpoint.channel} · "
            f"0x{route.response_endpoint.request_id:X}/"
            f"{format(route.response_endpoint.response_id, 'X') if route.response_endpoint.response_id is not None else '功能寻址'}",
            f"{route.source.sheet_name} 第 {route.source.row_number} 行") for route in routes
            if plan and any(route.source in operation.source_locations for operation in plan.operations))
        return FeatureSummaryDto(self.feature_id, self.display_name, _status(context), metrics, details=details)
