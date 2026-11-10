"""DaVinci 网关路由桥接服务。

桥接在内部端口上提供 MCP 端点，把客户端请求转发到独立核心。
它不实现任何路由规则，行为完全由核心决定。
"""

from davinci_gw.bridge.config import BridgeConfig, BridgeConfigError, CoreConfig
from davinci_gw.bridge.core import (
    BRIDGE_TOOL_NAMES,
    CoreClient,
    CoreUnavailableError,
    EmbeddedCore,
    HttpCore,
    StdioCore,
    build_core,
)
from davinci_gw.bridge.server import BRIDGE_VERSION, build_bridge_server

__all__ = [
    "BRIDGE_TOOL_NAMES",
    "BRIDGE_VERSION",
    "BridgeConfig",
    "BridgeConfigError",
    "CoreClient",
    "CoreConfig",
    "CoreUnavailableError",
    "EmbeddedCore",
    "HttpCore",
    "StdioCore",
    "build_bridge_server",
    "build_core",
]
