"""路由能力扩展点及默认能力集合。"""

from .base import FeatureContext, RoutingFeature
from .defaults import default_feature_registry
from .registry import DuplicateFeatureError, FeatureRegistry

__all__ = ["FeatureContext", "RoutingFeature", "DuplicateFeatureError", "FeatureRegistry", "default_feature_registry"]
