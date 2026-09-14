"""MCP tools/list 可见的稳定公共输出 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GatewayToolResponse(BaseModel):
    """所有生成与修复工具共享的必需信封；工具专属字段作为受控扩展保留。"""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["1.0"]
    tool: str
    operation_id: str
    status: Literal[
        "SUCCESS", "VALIDATION_FAILED", "CANCELLED", "SESSION_MISSING", "SESSION_EXPIRED",
        "SESSION_INVALID", "SESSION_CONSUMED", "INPUT_CHANGED", "INTERNAL_FAILURE",
    ]
    success: bool
    summary: str
    issue_count: int = Field(ge=0)
    issues_truncated: bool
    issues: list[dict[str, Any]]
