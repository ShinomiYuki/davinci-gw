"""定义与 Facade 解耦的路由能力协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from davinci_gw.contracts import FeatureSummaryDto, IssueDto
from davinci_gw.domain.models import MutationPlan, WorkbookData


@dataclass(frozen=True, slots=True)
class FeatureContext:
    """仅供能力实现读取的规划快照。"""

    workbook: WorkbookData | None
    plan: MutationPlan | None
    issues: tuple[IssueDto, ...] = ()


class RoutingFeature(Protocol):
    """显式注册功能必须提供的稳定标识、能力和摘要投影。"""
    feature_id: str
    display_name: str
    capabilities: tuple[str, ...]

    def summarize(self, context: FeatureContext) -> FeatureSummaryDto: ...
