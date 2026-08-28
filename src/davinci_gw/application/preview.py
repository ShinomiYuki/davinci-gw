"""生成不修改 ARXML 的结构化变更预览。"""

from __future__ import annotations

from pathlib import Path

from davinci_gw.domain.models import OperationType, PreviewReport, ValidationReport

from .generate import prepare_transaction


def preview_inputs(config_path: str | Path, baseline_path: str | Path) -> PreviewReport:
    """在内存投影上完成 DELETE/ADD 规划并展示真实状态，不执行文件写入。"""
    prepared = prepare_transaction(config_path, baseline_path)
    plan_issues = prepared.plan.issues if prepared.plan else ()
    validation = ValidationReport(
        prepared.validation.issues + plan_issues + prepared.issues,
        prepared.validation.workbook_data,
        prepared.validation.arxml_inspection,
    )
    data = validation.workbook_data
    if data is None:
        return PreviewReport(validation)
    return PreviewReport(
        validation=validation,
        target_version=data.target_version,
        reference_count=len(data.reference_data),
        direct_add_count=sum(route.operation is OperationType.ADD for route in data.direct_routes),
        direct_delete_count=sum(route.operation is OperationType.DELETE for route in data.direct_routes),
        signal_add_count=sum(route.operation is OperationType.ADD for route in data.signal_routes),
        signal_delete_count=sum(route.operation is OperationType.DELETE for route in data.signal_routes),
        plan=prepared.plan,
    )
