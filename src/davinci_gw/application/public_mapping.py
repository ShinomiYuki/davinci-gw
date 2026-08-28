"""将内部领域对象投影为无异常链、无 lxml 的公共 DTO。"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from pathlib import Path

from davinci_gw.contracts import IssueDto, JsonValue
from davinci_gw.domain.models import ValidationIssue


def _safe_actual(value: object) -> JsonValue:
    if isinstance(value, BaseException):
        return "<internal-error>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _safe_actual(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_safe_actual(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_actual(item) for key, item in value.items()}
    return "<unsupported>"


def issue_to_dto(issue: ValidationIssue) -> IssueDto:
    """有意忽略 cause，防止异常和 traceback 越过公共边界。"""
    location = issue.location
    message = (
        "内部处理失败；未公开异常详情，请通过受控日志诊断。"
        if issue.code.endswith("_SYSTEM_FAILED") else issue.message
    )
    return IssueDto(
        code=issue.code,
        message=message,
        severity=issue.severity.value,
        category=issue.category.value,
        file_path=str(issue.file_path) if issue.file_path else None,
        sheet_name=location.sheet_name if location else None,
        row_number=location.row_number if location else None,
        field_name=issue.field_name,
        actual_value=_safe_actual(issue.actual_value),
    )


def issues_to_dto(issues: tuple[ValidationIssue, ...]) -> tuple[IssueDto, ...]:
    return tuple(issue_to_dto(issue) for issue in issues)


def public_issue(code: str, message: str, *, category: str = "SYSTEM") -> IssueDto:
    return IssueDto(code=code, message=message, category=category)
