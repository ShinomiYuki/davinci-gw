"""公共 DTO 到有界 MCP JSON 信封的确定性映射。"""

from __future__ import annotations

import json
import math
from typing import Any, Protocol

from davinci_gw.contracts import OperationStatus

MAX_ISSUES = 200
MAX_RESPONSE_BYTES = 512_000
MAX_STRING = 4_096


class JsonSerializableDto(Protocol):
    """适配器所需的最小公共 DTO 能力。"""

    def to_dict(self) -> dict[str, Any]: ...


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "<unsupported>"
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    if isinstance(value, dict):
        return {str(key)[:128]: _bounded(item, depth=depth + 1) for key, item in list(value.items())[:200]}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:200]]
    return "<unsupported>"


def _issue_priority(issue: dict[str, Any]) -> tuple[int, str, str]:
    priority = {"ERROR": 0, "WARNING": 1, "INFO": 2}.get(str(issue.get("severity", "ERROR")).upper(), 3)
    return priority, str(issue.get("code", "")), str(issue.get("message", ""))


def issue(code: str, message: str, *, field_name: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "message": message,
        "severity": "ERROR",
        "category": "CONTRACT",
    }
    if field_name:
        result["field_name"] = field_name
    return result


def envelope(
    tool: str,
    *,
    status: str,
    operation_id: str,
    summary: str,
    issues: list[dict[str, Any]] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """创建稳定信封并执行错误优先、条数和总字节数限制。"""
    all_issues = sorted(issues or [], key=_issue_priority)
    selected = all_issues[:MAX_ISSUES]
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "tool": tool,
        "operation_id": operation_id,
        "status": status,
        "success": status == OperationStatus.SUCCESS.value,
        "summary": summary,
        "issue_count": len(all_issues),
        "issues_truncated": len(all_issues) > len(selected),
        "issues": selected,
    }
    result.update(payload)
    bounded = _bounded(result)
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_RESPONSE_BYTES:
        return bounded
    bounded["issues"] = bounded["issues"][:20]
    bounded["issues_truncated"] = True
    bounded["features"] = []
    bounded["response_truncated"] = True
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_RESPONSE_BYTES:
        return bounded
    return {
        "schema_version": "1.0", "tool": tool, "operation_id": operation_id,
        "status": "INTERNAL_FAILURE", "success": False,
        "summary": "结果超过安全响应上限，已拒绝返回大对象。",
        "issue_count": 1, "issues_truncated": True,
        "issues": [issue("RESPONSE_TOO_LARGE", "结果超过 512000 字节安全上限。")],
    }


def dto_envelope(tool: str, dto: JsonSerializableDto, summary: str, **payload: Any) -> dict[str, Any]:
    data = dto.to_dict()
    raw_issues = data.pop("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = []
    raw_status = data.pop("status", "INTERNAL_FAILURE")
    status = raw_status.value if isinstance(raw_status, OperationStatus) else str(raw_status)
    operation_id = str(data.pop("operation_id", ""))
    data.pop("schema_version", None)
    data.update(payload)
    return envelope(
        tool, status=status, operation_id=operation_id, summary=summary,
        issues=[item for item in raw_issues if isinstance(item, dict)], **data,
    )
