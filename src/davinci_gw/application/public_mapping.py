"""将内部领域对象投影为无异常链、无 lxml 的公共 DTO。"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from pathlib import Path

from davinci_gw.contracts import CheckResultDto, IssueDto, JsonValue, MessageIdentityDto
from davinci_gw.domain.models import ArxmlInspectionResult, RetentionDecision, ValidationIssue, WorkbookData


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


def _route_messages(workbook: WorkbookData | None) -> dict[tuple[str | None, int | None], tuple[MessageIdentityDto, ...]]:
    """按工作表和行号建立报文身份索引，不重新读取输入文件。"""
    if workbook is None:
        return {}
    result: dict[tuple[str | None, int | None], tuple[MessageIdentityDto, ...]] = {}
    for route in workbook.direct_routes:
        result[(route.source.sheet_name, route.source.row_number)] = (
            MessageIdentityDto("SOURCE", route.key.source_message_name, route.source_can_id_text),
            MessageIdentityDto("TARGET", route.key.target_message_name, route.target_can_id_text),
        )
    for route in workbook.signal_routes:
        result[(route.source.sheet_name, route.source.row_number)] = (
            MessageIdentityDto("SOURCE", route.key.source_message_name),
            MessageIdentityDto("TARGET", route.key.target_message_name),
        )
    return result


def _field_message(issue: ValidationIssue) -> tuple[MessageIdentityDto, ...]:
    """路由行尚未形成领域对象时，仍保留当前出错字段可证明的报文身份。"""
    field = issue.field_name or ""
    if "报文" not in field or not field.startswith(("源", "目标")):
        return ()
    role = "SOURCE" if field.startswith("源") else "TARGET"
    if "CANID" in field:
        value = issue.actual_value
        if value is None:
            return ()
        can_id = f"0x{value:X}" if isinstance(value, int) and not isinstance(value, bool) else str(value)
        return (MessageIdentityDto(role, can_id=can_id),)
    if "名称" in field and issue.actual_value is not None:
        return (MessageIdentityDto(role, name=str(issue.actual_value)),)
    return ()


def issue_to_dto(
    issue: ValidationIssue,
    messages: tuple[MessageIdentityDto, ...] = (),
) -> IssueDto:
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
        messages=messages or _field_message(issue),
    )


def issues_to_dto(
    issues: tuple[ValidationIssue, ...],
    workbook: WorkbookData | None = None,
) -> tuple[IssueDto, ...]:
    message_index = _route_messages(workbook)
    return tuple(issue_to_dto(
        issue,
        message_index.get(
            (issue.location.sheet_name, issue.location.row_number), (),
        ) if issue.location else (),
    ) for issue in issues)


def decisions_to_dto(
    decisions: tuple[RetentionDecision, ...],
    workbook: WorkbookData | None = None,
) -> tuple[IssueDto, ...]:
    """把保守保留决定加入通用问题表，同时保持其非阻塞语义。"""
    result = []
    message_index = _route_messages(workbook)
    for decision in decisions:
        location = decision.source_locations[0] if decision.source_locations else None
        result.append(IssueDto(
            code=f"RETENTION_{decision.category}",
            message=f"{decision.reason}（对象：{decision.object_path}）",
            severity="DECISION",
            category="DECISION",
            sheet_name=location.sheet_name if location else None,
            row_number=location.row_number if location else None,
            actual_value=decision.object_path,
            messages=message_index.get(
                (location.sheet_name, location.row_number), (),
            ) if location else (),
        ))
    return tuple(result)


def public_issue(code: str, message: str, *, category: str = "SYSTEM") -> IssueDto:
    return IssueDto(code=code, message=message, category=category)


def inspection_checks(inspection: ArxmlInspectionResult | None) -> tuple[CheckResultDto, ...]:
    """把 ARXML 检查投影为不依赖固定界面字段的公共结果。"""
    if inspection is None:
        return ()
    schema = inspection.schema_filename or inspection.schema_location or inspection.namespace_uri
    checks = [CheckResultDto("arxml_schema", "AUTOSAR Schema", "PASS", schema)]
    checks.extend(
        CheckResultDto(
            f"module_{module_name.lower()}",
            f"{module_name} 模块",
            "PASS",
            module.autosar_path,
        )
        for module_name, module in inspection.modules.items()
    )
    return tuple(checks)
