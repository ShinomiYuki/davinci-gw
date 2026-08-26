"""与 Excel、ARXML、CLI 和 GUI 无关的网关路由领域定义。"""

from .models import (
    ArxmlInspectionResult,
    ArxmlModuleInfo,
    DirectRouteChange,
    OperationType,
    PreviewReport,
    ReferenceDataEntry,
    SignalRouteChange,
    SourceLocation,
    ValidationCategory,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
    WorkbookData,
    WorkbookReadResult,
)
from .route_keys import DirectRouteKey, SignalRouteKey

__all__ = [
    "ArxmlInspectionResult", "ArxmlModuleInfo", "DirectRouteChange", "DirectRouteKey",
    "OperationType", "PreviewReport", "ReferenceDataEntry", "SignalRouteChange",
    "SignalRouteKey", "SourceLocation", "ValidationCategory", "ValidationIssue",
    "ValidationReport", "ValidationSeverity", "WorkbookData", "WorkbookReadResult",
]
