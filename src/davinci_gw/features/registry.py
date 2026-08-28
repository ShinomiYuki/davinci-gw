"""提供实例隔离、确定排序和重复检测的路由能力注册表。"""

from __future__ import annotations

from davinci_gw.contracts import FeatureCapabilityDto, FeatureSummaryDto

from .base import FeatureContext, RoutingFeature


class DuplicateFeatureError(ValueError):
    """功能标识重复，注册表保持原状态。"""
    pass


class FeatureRegistry:
    """实例隔离且按显式注册顺序输出功能的注册表。"""
    def __init__(self, features: tuple[RoutingFeature, ...] = ()) -> None:
        self._features: dict[str, RoutingFeature] = {}
        for feature in features:
            self.register(feature)

    def register(self, feature: RoutingFeature) -> None:
        """注册一个稳定标识功能，并拒绝覆盖已有功能。"""
        if not isinstance(feature.feature_id, str) or not feature.feature_id.strip():
            raise ValueError("路由能力必须提供非空字符串 feature_id。")
        if not isinstance(feature.display_name, str) or not feature.display_name.strip():
            raise ValueError(f"路由能力 {feature.feature_id} 必须提供显示名称。")
        if not isinstance(feature.capabilities, tuple) or not all(
            isinstance(item, str) for item in feature.capabilities
        ):
            raise ValueError(f"路由能力 {feature.feature_id} 的 capabilities 必须是字符串元组。")
        if feature.feature_id in self._features:
            raise DuplicateFeatureError(f"路由能力标识重复：{feature.feature_id}")
        self._features[feature.feature_id] = feature

    def capabilities(self) -> tuple[FeatureCapabilityDto, ...]:
        """按注册顺序投影公共能力。"""
        return tuple(
            FeatureCapabilityDto(item.feature_id, item.display_name, item.capabilities)
            for item in self._features.values()
        )

    def summarize(self, context: FeatureContext) -> tuple[FeatureSummaryDto, ...]:
        """让每个功能独立生成动态摘要。"""
        summaries: list[FeatureSummaryDto] = []
        for feature in self._features.values():
            summary = feature.summarize(context)
            if not isinstance(summary, FeatureSummaryDto):
                raise TypeError(f"路由能力 {feature.feature_id} 未返回 FeatureSummaryDto。")
            if summary.feature_id != feature.feature_id:
                raise ValueError(f"路由能力 {feature.feature_id} 返回了不一致的 feature_id。")
            summaries.append(summary)
        return tuple(summaries)
