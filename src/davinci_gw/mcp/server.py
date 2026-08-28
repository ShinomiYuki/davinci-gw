"""官方 MCP SDK 2.x 的纯 STDIO 服务定义。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from davinci_gw import __version__
from davinci_gw.mcp.schemas import GatewayToolResponse
from davinci_gw.mcp.service import GatewayMcpService

SERVER_INSTRUCTIONS = (
    "这是纯本地 STDIO 网关配置服务。标准工作流：先调用 validate_gateway_inputs，"
    "再调用 preview_gateway_update；向用户展示预览摘要并取得明确确认后，才调用 "
    "generate_gateway_arxml。生成必须使用同一进程内预览返回的 preparation_id，并指定尚不存在的"
    "新 .arxml 输出路径。所有路径必须是带盘符的本地 Windows 绝对路径；禁止 URL、UNC、设备路径、"
    "网络共享和覆盖基准或已有文件。工具只返回有界 JSON 摘要，禁止请求或返回 ARXML 文件内容。"
)


def build_server(service: GatewayMcpService | None = None) -> MCPServer[Any]:
    """建立只注册四个稳定工具的服务；传入 service 便于协议测试。"""
    gateway = service or GatewayMcpService()

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncGenerator[None, None]:
        try:
            yield None
        finally:
            await gateway.close()

    server: MCPServer[Any] = MCPServer(
        name="davinci-gw-mcp",
        title="DaVinci 网关配置本地服务",
        description="通过本地 Excel 路由配置安全预览并生成新的 DaVinci ARXML。",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        log_level="WARNING",
    )

    @server.tool(
        name="get_gateway_capabilities",
        description="查询本地服务版本、路由能力、Prepared Session 与路径安全策略；不读取用户文件。",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
        ),
        structured_output=True,
    )
    def get_gateway_capabilities() -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(gateway.get_gateway_capabilities())

    @server.tool(
        name="validate_gateway_inputs",
        description="只读校验一个本地 .xlsx 配置表和一个本地 .arxml 基准文件；路径必须为绝对路径。",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
        ),
        structured_output=True,
    )
    async def validate_gateway_inputs(config_path: str, baseline_path: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(
            await gateway.validate_gateway_inputs(config_path, baseline_path),
        )

    @server.tool(
        name="preview_gateway_update",
        description="解析并预览网关变更，创建当前进程内一次性 preparation_id；不写输出文件。",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=False,
        ),
        structured_output=True,
    )
    async def preview_gateway_update(config_path: str, baseline_path: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(
            await gateway.preview_gateway_update(config_path, baseline_path),
        )

    @server.tool(
        name="generate_gateway_arxml",
        description=(
            "在用户确认预览后，使用 preparation_id 原子生成一个尚不存在的新 .arxml 文件；禁止覆盖。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False,
        ),
        structured_output=True,
    )
    async def generate_gateway_arxml(preparation_id: str, output_path: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(
            await gateway.generate_gateway_arxml(preparation_id, output_path),
        )

    return server
