"""编排配置表与基线 ARXML 校验，以及无修改往返写出。"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import ArxmlStructureError
from davinci_gw.domain.models import (
    ArxmlInspectionResult,
    ValidationCategory,
    ValidationIssue,
    ValidationReport,
)
from davinci_gw.input.workbook_reader import read_workbook


def inspect_baseline(baseline_path: str | Path) -> ArxmlInspectionResult:
    """安全加载基准 ARXML 并检查命名空间、Schema 和四个目标模块。"""
    return ArxmlDocument.load(baseline_path).inspect()


def write_roundtrip_copy(
    baseline_path: str | Path, output_path: str | Path, overwrite: bool = False,
) -> Path:
    """无修改地安全写出完整 ARXML；该接口不代表已经应用路由配置。"""
    return ArxmlDocument.load(baseline_path).write_atomic(output_path, overwrite=overwrite)


def _missing_file(path: Path, label: str) -> ValidationIssue:
    return ValidationIssue(
        code="INPUT_FILE_MISSING",
        message=f"{label}“{path}”不存在，请检查路径后重试。",
        file_path=path,
    )


def _system_issue(
    path: Path, label: str, detail: str, cause: BaseException | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code="INPUT_FILE_DAMAGED",
        message=f"{label}“{path}”{detail}，请确认文件完整、未被占用且格式正确后重试。",
        category=ValidationCategory.SYSTEM,
        file_path=path,
        cause=cause,
    )


def validate_inputs(config_path: str | Path, baseline_path: str | Path) -> ValidationReport:
    """联合校验两个输入文件并返回结构化结果，不打印或退出进程。"""
    config = Path(config_path).expanduser().resolve()
    baseline = Path(baseline_path).expanduser().resolve()
    issues: list[ValidationIssue] = []
    workbook_data = None
    inspection = None

    if not config.is_file():
        issues.append(_missing_file(config, "配置表"))
    else:
        try:
            workbook_result = read_workbook(config)
            workbook_data = workbook_result.data
            issues.extend(workbook_result.issues)
        except Exception as exc:
            # 原始异常保存在校验项中，普通模式只显示中文提示，--debug 可输出异常链。
            issues.append(_system_issue(config, "配置表", "无法读取", exc))

    if not baseline.is_file():
        issues.append(_missing_file(baseline, "基准ARXML"))
    else:
        try:
            inspection = inspect_baseline(baseline)
        except ArxmlStructureError as exc:
            issues.append(ValidationIssue(
                code="ARXML_STRUCTURE_INVALID", message=str(exc), file_path=baseline,
            ))
        except (etree.XMLSyntaxError, OSError, ValueError) as exc:
            issues.append(_system_issue(baseline, "基准ARXML", "无法解析", exc))

    return ValidationReport(tuple(issues), workbook_data, inspection)
