"""与 Excel、lxml 和 CLI 无关的核心领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .route_keys import DirectRouteKey, SignalRouteKey


class OperationType(str, Enum):
    """标准变更操作。"""

    ADD = "ADD"
    DELETE = "DELETE"


class ValidationSeverity(str, Enum):
    """校验问题严重级别。"""

    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationCategory(str, Enum):
    """用于稳定区分用户契约错误和系统/文件损坏错误。"""

    CONTRACT = "CONTRACT"
    SYSTEM = "SYSTEM"


class MutationKind(str, Enum):
    """按模块解耦的配置对象或参数类型。"""

    ECUC_PDU = "ECUC_PDU"
    CANIF_RX_PDU = "CANIF_RX_PDU"
    CANIF_TX_PDU = "CANIF_TX_PDU"
    PDUR_ROUTING_PATH = "PDUR_ROUTING_PATH"
    PDUR_SRC_PDU = "PDUR_SRC_PDU"
    PDUR_DEST_PDU = "PDUR_DEST_PDU"
    COM_GW_MAPPING = "COM_GW_MAPPING"
    COM_GW_SOURCE = "COM_GW_SOURCE"
    COM_GW_DESTINATION = "COM_GW_DESTINATION"
    COM_SIGNAL_TIMEOUT = "COM_SIGNAL_TIMEOUT"


class MutationAction(str, Enum):
    """显式声明变更方向，禁止再用 SHORT-NAME 是否为空推断行为。"""

    CREATE = "CREATE"
    REMOVE = "REMOVE"
    UPSERT_PARAMETERS = "UPSERT_PARAMETERS"
    REMOVE_PARAMETERS = "REMOVE_PARAMETERS"
    RETAIN = "RETAIN"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """外部输入中的可定位来源。"""

    sheet_name: str | None = None
    row_number: int | None = None


@dataclass(frozen=True, slots=True)
class ReferenceDataEntry:
    """一个 CAN 通道及后续轮次使用的 CanIf 引用数据。"""

    channel_name: str
    tx_buffer_name: str | None
    hrh_name: str | None
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class DirectRouteChange:
    """一条直接报文路由变更及本轮解析的全部参数。"""

    operation: OperationType
    key: DirectRouteKey
    source_length: int | None
    source_message_type: str | None
    source_rx_indication_ul: str | None
    source_checksum_enabled: str | None
    source_dlc_check_enabled: str | None
    target_length: int | None
    target_message_type: str | None
    target_checksum_enabled: str | None
    target_pn_filter_enabled: str | None
    target_truncation_enabled: str | None
    length_strategy: str | None
    source: SourceLocation

    @property
    def source_can_id_text(self) -> str:
        """以统一十六进制格式回显源 CAN ID。"""
        return f"0x{self.key.source_can_id:X}"

    @property
    def target_can_id_text(self) -> str:
        """以统一十六进制格式回显目标 CAN ID。"""
        return f"0x{self.key.target_can_id:X}"


@dataclass(frozen=True, slots=True)
class SignalRouteChange:
    """一条信号路由变更及允许为空的超时信息。"""

    operation: OperationType
    key: SignalRouteKey
    byte_order: str | None
    timeout_value: Decimal | None
    timeout_time: Decimal | None
    source_signal_group_name: str | None
    source: SourceLocation


@dataclass(frozen=True, slots=True)
class WorkbookData:
    """标准工作簿规范化后的全部执行数据。"""

    path: Path
    target_version: str | None
    reference_data: tuple[ReferenceDataEntry, ...] = ()
    direct_routes: tuple[DirectRouteChange, ...] = ()
    signal_routes: tuple[SignalRouteChange, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """可供 CLI 和未来 GUI 共同渲染的结构化问题。"""

    code: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    category: ValidationCategory = ValidationCategory.CONTRACT
    file_path: Path | None = None
    location: SourceLocation | None = None
    field_name: str | None = None
    actual_value: object = None
    cause: BaseException | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ArxmlModuleInfo:
    """目标 ECUC 模块的位置与标识；node 仅由 ARXML 层使用。"""

    short_name: str
    definition_ref: str
    autosar_path: str
    package_path: str
    node: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ArxmlInspectionResult:
    """基准 ARXML 的命名空间、Schema 和四模块检查结果。"""

    path: Path
    namespace_uri: str
    schema_location: str | None
    schema_filename: str | None
    modules: Mapping[str, ArxmlModuleInfo]

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", MappingProxyType(dict(self.modules)))


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """输入联合校验结果。"""

    issues: tuple[ValidationIssue, ...] = ()
    workbook_data: WorkbookData | None = None
    arxml_inspection: ArxmlInspectionResult | None = None

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """返回所有阻止执行的问题。"""
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """返回所有不阻止执行的提示。"""
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        """没有错误时输入有效。"""
        return not self.errors


@dataclass(frozen=True, slots=True)
class PreviewReport:
    """在可丢弃投影树上完成规划后的结构化变更预览。"""

    validation: ValidationReport
    target_version: str | None = None
    reference_count: int = 0
    direct_add_count: int = 0
    direct_delete_count: int = 0
    signal_add_count: int = 0
    signal_delete_count: int = 0
    plan: "MutationPlan | None" = None

    @property
    def is_valid(self) -> bool:
        """预览所基于的联合校验是否通过。"""
        return self.validation.is_valid


@dataclass(frozen=True, slots=True)
class WorkbookReadResult:
    """读取器返回的数据与契约问题集合。"""

    data: WorkbookData
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        """工作簿契约是否通过。"""
        return not any(issue.severity is ValidationSeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class MutationOperation:
    """与 lxml 无关的创建、删除、参数变更或保留验证操作。"""

    kind: MutationKind
    parent_path: str
    short_name: str
    definition_ref: str
    action: MutationAction = MutationAction.CREATE
    parameters: tuple[tuple[str, str], ...] = ()
    references: tuple[tuple[str, str], ...] = ()
    source_locations: tuple[SourceLocation, ...] = ()

    @property
    def object_path(self) -> str:
        """返回操作完成后的完整 AUTOSAR 路径。"""
        return (f"{self.parent_path.rstrip('/')}/{self.short_name}"
                if self.short_name else self.parent_path)

    def parameter(self, definition_ref: str) -> str | None:
        """按定义引用读取计划参数。"""
        return dict(self.parameters).get(definition_ref)

    def reference(self, definition_ref: str) -> str | None:
        """按定义引用读取计划引用。"""
        return dict(self.references).get(definition_ref)


@dataclass(frozen=True, slots=True)
class MutationPlan:
    """完整 ADD/DELETE 事务预检后的不可变变更计划。"""

    operations: tuple[MutationOperation, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    direct_added_count: int = 0
    direct_existing_count: int = 0
    direct_skipped_count: int = 0
    signal_added_count: int = 0
    signal_existing_count: int = 0
    signal_skipped_count: int = 0
    direct_deleted_count: int = 0
    direct_missing_count: int = 0
    direct_retained_count: int = 0
    direct_conflict_count: int = 0
    signal_deleted_count: int = 0
    signal_missing_count: int = 0
    signal_retained_count: int = 0
    signal_timeout_removed_count: int = 0
    signal_timeout_retained_count: int = 0
    signal_conflict_count: int = 0
    expected_new_uuids: tuple[str, ...] = ()
    decisions: tuple["RetentionDecision", ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """返回阻止整个生成过程的问题。"""
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def expected_paths(self) -> tuple[str, ...]:
        """返回输出验证必须能唯一定位的新增对象路径。"""
        return tuple(operation.object_path for operation in self.operations
                     if operation.action is MutationAction.CREATE)

    @property
    def expected_internal_references(self) -> tuple[str, ...]:
        """返回新增操作中应在本文件内解析的 VALUE-REF 目标。"""
        return tuple(value for operation in self.operations for _, value in operation.references)

    @property
    def removed_paths(self) -> tuple[str, ...]:
        """返回输出中必须不存在的容器路径。"""
        return tuple(operation.object_path for operation in self.operations
                     if operation.action is MutationAction.REMOVE)


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    """记录共享对象或超时被保守保留的路径、原因和输入来源。"""

    object_path: str
    reason: str
    category: str
    source_locations: tuple[SourceLocation, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """generate 应用接口和 CLI 共同使用的结构化结果。"""

    validation: ValidationReport
    plan: MutationPlan | None = None
    output_path: Path | None = None
    output_written: bool = False
    output_validated: bool = False
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def all_issues(self) -> tuple[ValidationIssue, ...]:
        """合并输入校验、规划和输出阶段问题。"""
        planned = self.plan.issues if self.plan else ()
        return self.validation.issues + planned + self.issues

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """返回所有阻断问题。"""
        return tuple(issue for issue in self.all_issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """返回已跳过路由和其他非阻断提示。"""
        return tuple(issue for issue in self.all_issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def is_success(self) -> bool:
        """输出已原子写出并通过复核时生成成功。"""
        return not self.errors and self.output_written and self.output_validated
