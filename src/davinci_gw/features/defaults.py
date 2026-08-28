"""直接报文和信号路由的默认动态统计实现。"""

from __future__ import annotations

from dataclasses import dataclass

from davinci_gw.contracts import FeatureSummaryDto, MetricDto
from davinci_gw.domain.models import OperationType

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
        return FeatureSummaryDto(self.feature_id, self.display_name, _status(context), metrics)


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
        return FeatureSummaryDto(self.feature_id, self.display_name, _status(context), metrics)


def default_feature_registry() -> FeatureRegistry:
    """为每个 Facade 创建独立默认注册表，避免全局测试污染。"""
    return FeatureRegistry((DirectMessageFeature(), SignalRoutingFeature()))
