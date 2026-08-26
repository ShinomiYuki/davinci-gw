"""生成不修改 ARXML 的结构化变更预览。"""

from __future__ import annotations

from pathlib import Path

from davinci_gw.domain.models import OperationType, PreviewReport

from .validate import validate_inputs


def preview_inputs(config_path: str | Path, baseline_path: str | Path) -> PreviewReport:
    """复用联合校验结果统计 ADD、DELETE 和引用数据，不执行任何写入。"""
    validation = validate_inputs(config_path, baseline_path)
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
    )
