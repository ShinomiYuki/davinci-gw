"""官方 MCP SDK 2.x 的纯 STDIO 服务定义。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from davinci_gw.mcp.schemas import GatewayToolResponse
from davinci_gw.mcp.service import GatewayMcpService
from davinci_gw.mcp.version import MCP_VERSION

SERVER_INSTRUCTIONS = (
    "这是纯本地 STDIO 网关配置服务。标准工作流：先调用 validate_gateway_inputs，"
    "再调用 preview_gateway_update；向用户展示预览摘要并取得明确确认后，才调用 "
    "generate_gateway_arxml。生成必须使用同一进程内预览返回的 preparation_id，并指定尚不存在的"
    "新 .arxml 输出路径。所有路径必须是带盘符的本地 Windows 绝对路径；禁止 URL、UNC、设备路径、"
    "网络共享和覆盖基准或已有文件。工具只返回有界 JSON 摘要，禁止请求或返回 ARXML 文件内容。"
    "若生成被阻断，先调用 diagnose_generation_failure；只有结论为具备稳定复现和代码证据的工具 BUG，"
    "并取得用户第一次明确确认后，才能调用 start_bug_repair。修复完成后仍须取得第二次明确确认，"
    "才能调用 submit_bug_repair；DBC 缺失、输入问题、基线问题和不确定问题均禁止修改代码。"
)


def build_server(service: GatewayMcpService | None = None) -> MCPServer[Any]:
    """建立网关生成和两阶段审批修复工具；传入 service 便于协议测试。"""
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
        version=MCP_VERSION,
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

    @server.tool(
        name="diagnose_generation_failure",
        description=(
            "使用原始 Excel/ARXML 稳定复现并区分 DBC 缺失、输入、基线、工具 BUG 或不确定；"
            "只读诊断，不创建分支或修改文件。repository_path 必须包含完整源码。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=True,
        ),
        structured_output=True,
    )
    async def diagnose_generation_failure(
        config_path: str, baseline_path: str, repository_path: str, python_path: str,
    ) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(await gateway.diagnose_generation_failure(
            config_path, baseline_path, repository_path, python_path,
        ))

    @server.tool(
        name="start_bug_repair",
        description=(
            "仅对确认的工具 BUG，在用户提供指定确认语后创建隔离热修复分支和 worktree，"
            "后台完成修改、审查、测试、原输入复验与候选包构建。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True,
        ),
        structured_output=True,
    )
    async def start_bug_repair(diagnosis_id: str, confirmation: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(await gateway.start_bug_repair(diagnosis_id, confirmation))

    @server.tool(
        name="get_bug_repair_status",
        description="查询后台热修复阶段、测试、候选包和待确认状态；不推进提交。",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
        ),
        structured_output=True,
    )
    def get_bug_repair_status(repair_id: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(gateway.get_bug_repair_status(repair_id))

    @server.tool(
        name="submit_bug_repair",
        description=(
            "仅在全部修复验证通过且用户提供第二次指定确认语后执行正式提交；"
            "支持仅本地提交、外部贡献者 Draft PR 或维护者合并发布。"
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True,
        ),
        structured_output=True,
    )
    async def submit_bug_repair(
        repair_id: str,
        confirmation: str,
        mode: str,
        commit_message: str,
        remote_name: str = "origin",
        upstream_repository: str = "",
        fork_owner: str = "",
    ) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(await gateway.submit_bug_repair(
            repair_id, confirmation, mode, commit_message, remote_name, upstream_repository, fork_owner,
        ))

    @server.tool(
        name="cancel_bug_repair",
        description="使用指定确认语取消活动修复；保留隔离 worktree 和报告供人工排查。",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False,
        ),
        structured_output=True,
    )
    async def cancel_bug_repair(repair_id: str, confirmation: str) -> GatewayToolResponse:
        return GatewayToolResponse.model_validate(await gateway.cancel_bug_repair(repair_id, confirmation))

    return server
