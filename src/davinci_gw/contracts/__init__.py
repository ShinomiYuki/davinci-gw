"""供 GUI、本地 MCP 和其他前端共同使用的稳定公共契约。"""

from .models import (
    SCHEMA_VERSION,
    ArtifactDto,
    CapabilitiesDto,
    FeatureCapabilityDto,
    FeatureSummaryDto,
    FileFingerprintDto,
    GenerationResultDto,
    InputFileDto,
    IssueDto,
    JsonValue,
    MetricDto,
    OperationResultDto,
    OperationStatus,
    PreparedSessionDto,
    PreviewResultDto,
    ProgressEventDto,
    SessionState,
    UpdateRequestDto,
)

__all__ = [
    "SCHEMA_VERSION", "ArtifactDto", "CapabilitiesDto", "FeatureCapabilityDto",
    "FeatureSummaryDto", "FileFingerprintDto", "GenerationResultDto", "InputFileDto", "IssueDto", "JsonValue",
    "MetricDto", "OperationResultDto", "OperationStatus", "PreparedSessionDto",
    "PreviewResultDto", "ProgressEventDto", "SessionState", "UpdateRequestDto",
]
