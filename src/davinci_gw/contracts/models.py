"""不可变、可稳定序列化且不暴露内部实现对象的公共 DTO。"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

SCHEMA_VERSION = "1.0"
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _json_safe(value: object) -> JsonValue:
    """递归转换为 JSON 原生类型，并拒绝异常对象泄漏。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return "<internal-error>"
    if is_dataclass(value) and not isinstance(value, type):
        if not isinstance(value, SerializableDto):
            return "<unsupported>"
        return {item.name: _json_safe(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_safe(item) for item in value]
    return "<unsupported>"


class SerializableDto:
    """为全部公共 DTO 提供确定字段顺序的序列化。"""

    def to_dict(self) -> dict[str, JsonValue]:
        value = _json_safe(self)
        if not isinstance(value, dict):  # pragma: no cover
            raise TypeError("DTO 必须序列化为 JSON 对象。")
        return value

    def to_json(self) -> str:
        """以稳定紧凑格式输出 UTF-8 友好的 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class OperationStatus(str, Enum):
    """公共操作的稳定终态，适配器不得从消息文本推断状态。"""
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CANCELLED = "CANCELLED"
    SESSION_MISSING = "SESSION_MISSING"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_INVALID = "SESSION_INVALID"
    SESSION_CONSUMED = "SESSION_CONSUMED"
    INPUT_CHANGED = "INPUT_CHANGED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class SessionState(str, Enum):
    """Prepared Session 的公共生命周期状态。"""
    READY = "READY"
    COMMITTING = "COMMITTING"
    CONSUMED = "CONSUMED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class UpdateRequestDto(SerializableDto):
    """校验、预览或生成所需的通用文件请求。"""
    config_path: str
    baseline_path: str
    output_path: str | None = None
    overwrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_path", str(self.config_path))
        object.__setattr__(self, "baseline_path", str(self.baseline_path))
        if self.output_path is not None:
            object.__setattr__(self, "output_path", str(self.output_path))


@dataclass(frozen=True, slots=True)
class FileFingerprintDto(SerializableDto):
    """以大小、修改时间和内容摘要标识一个输入快照。"""
    path: str
    size: int
    modified_ns: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))


@dataclass(frozen=True, slots=True)
class InputFileDto(SerializableDto):
    """描述输入角色、规范路径及可选指纹。"""
    role: str
    path: str
    fingerprint: FileFingerprintDto | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))


@dataclass(frozen=True, slots=True)
class IssueDto(SerializableDto):
    """前端可直接定位和展示、且不携带异常对象的问题。"""
    code: str
    message: str
    severity: str = "ERROR"
    category: str = "CONTRACT"
    file_path: str | None = None
    sheet_name: str | None = None
    row_number: int | None = None
    field_name: str | None = None
    actual_value: JsonValue = None

    def __post_init__(self) -> None:
        if self.file_path is not None:
            object.__setattr__(self, "file_path", str(self.file_path))
        object.__setattr__(self, "actual_value", _json_safe(self.actual_value))


@dataclass(frozen=True, slots=True)
class MetricDto(SerializableDto):
    """功能无关的单个统计指标。"""
    key: str
    label: str
    value: JsonScalar

    def __post_init__(self) -> None:
        value = _json_safe(self.value)
        object.__setattr__(
            self, "value", value if value is None or isinstance(value, (str, int, float, bool)) else "<unsupported>",
        )


@dataclass(frozen=True, slots=True)
class FeatureSummaryDto(SerializableDto):
    """按功能动态组织状态、指标和问题，避免固定路由字段。"""
    feature_id: str
    display_name: str
    status: str
    metrics: tuple[MetricDto, ...] = ()
    issues: tuple[IssueDto, ...] = ()
    metadata: Mapping[str, JsonValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        normalized = _json_safe(self.metadata)
        object.__setattr__(
            self, "metadata", MappingProxyType(normalized if isinstance(normalized, dict) else {}),
        )


@dataclass(frozen=True, slots=True)
class FeatureCapabilityDto(SerializableDto):
    """一个已注册路由功能的稳定能力声明。"""
    feature_id: str
    display_name: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressEventDto(SerializableDto):
    """与 GUI/MCP 技术无关的阶段进度快照。"""
    operation_id: str
    sequence: int
    stage_id: str
    stage_name: str
    current: int
    total: int
    percent: float
    cancellable: bool = True
    feature_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDto(SerializableDto):
    """已原子发布输出的路径、大小和内容摘要。"""
    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))


@dataclass(frozen=True, slots=True)
class CapabilitiesDto(SerializableDto):
    """Facade 当前注册的功能及公开操作。"""
    features: tuple[FeatureCapabilityDto, ...]
    operations: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION
    session_ttl_seconds: float = 900.0
    session_capacity: int = 8


@dataclass(frozen=True, slots=True)
class PreviewResultDto(SerializableDto):
    """预处理所得公共摘要，不包含内部工作树或计划。"""
    operation_id: str
    status: OperationStatus
    features: tuple[FeatureSummaryDto, ...] = ()
    issues: tuple[IssueDto, ...] = ()
    input_files: tuple[InputFileDto, ...] = ()
    target_version: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PreparedSessionDto(SerializableDto):
    """可供确认提交的不透明会话信息。"""
    operation_id: str
    status: OperationStatus
    session_id: str | None = None
    session_state: SessionState | None = None
    created_at: str | None = None
    expires_at: str | None = None
    preview: PreviewResultDto | None = None
    issues: tuple[IssueDto, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class OperationResultDto(SerializableDto):
    """校验、释放和清理等通用操作结果。"""
    operation_id: str
    status: OperationStatus
    issues: tuple[IssueDto, ...] = ()
    features: tuple[FeatureSummaryDto, ...] = ()
    artifact: ArtifactDto | None = None
    session_id: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GenerationResultDto(SerializableDto):
    """生成终态、动态统计和已发布产物信息。"""
    operation_id: str
    status: OperationStatus
    issues: tuple[IssueDto, ...] = ()
    features: tuple[FeatureSummaryDto, ...] = ()
    artifact: ArtifactDto | None = None
    session_id: str | None = None
    schema_version: str = SCHEMA_VERSION
