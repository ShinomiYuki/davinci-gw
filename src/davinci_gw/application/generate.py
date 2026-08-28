"""统一 DELETE/ADD 投影事务、输出验证和原子写出。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import (
    ArxmlStructureError,
    OutputValidationError,
    OutputWriteError,
)
from davinci_gw.domain.models import (
    GenerationReport,
    MutationPlan,
    ValidationCategory,
    ValidationIssue,
    ValidationReport,
)
from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.routing.transaction import TransactionCoordinator
from davinci_gw.validation.output import validate_generated_output

from .validate import _missing_file, _system_issue


def _generation_issue(code: str, message: str, path: Path | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, file_path=path)


@dataclass(slots=True)
class PreparedTransaction:
    """输入校验、完整事务计划以及已应用到可丢弃工作树的结果。"""

    validation: ValidationReport
    plan: MutationPlan | None = None
    document: ArxmlDocument | None = None
    issues: tuple[ValidationIssue, ...] = ()


def prepare_transaction(
    config_path: str | Path, baseline_path: str | Path,
    checkpoint: Callable[[str], None] | None = None,
) -> PreparedTransaction:
    """只读加载输入并在内存工作树完成 DELETE 投影和 ADD，不写任何文件。"""
    config = Path(config_path).expanduser().resolve()
    baseline = Path(baseline_path).expanduser().resolve()
    issues: list[ValidationIssue] = []
    workbook_data = None
    inspection = None
    document = None
    check = checkpoint or (lambda _stage: None)

    check("before_workbook")
    if not config.is_file():
        issues.append(_missing_file(config, "配置表"))
    else:
        try:
            workbook_result = read_workbook(config)
            workbook_data = workbook_result.data
            issues.extend(workbook_result.issues)
        except Exception as exc:
            issues.append(_system_issue(config, "配置表", "无法读取", exc))
    check("after_workbook")
    if not baseline.is_file():
        issues.append(_missing_file(baseline, "基准ARXML"))
    else:
        try:
            document = ArxmlDocument.load(baseline)
            inspection = document.inspect()
        except ArxmlStructureError as exc:
            issues.append(_generation_issue("ARXML_STRUCTURE_INVALID", str(exc), baseline))
        except (etree.XMLSyntaxError, OSError, ValueError) as exc:
            issues.append(_system_issue(baseline, "基准ARXML", "无法解析", exc))
    check("after_baseline")

    validation = ValidationReport(tuple(issues), workbook_data, inspection)
    if not validation.is_valid or workbook_data is None or document is None:
        return PreparedTransaction(validation)
    check("before_plan")
    try:
        plan = TransactionCoordinator(document, workbook_data).plan_and_apply()
    except ArxmlStructureError as exc:
        return PreparedTransaction(
            validation, document=document,
            issues=(_generation_issue("ARXML_TRANSACTION_STRUCTURE_INVALID", str(exc), baseline),),
        )
    except Exception as exc:
        return PreparedTransaction(
            validation, document=document,
            issues=(ValidationIssue(
                code="ARXML_TRANSACTION_SYSTEM_FAILED",
                message=f"执行 DELETE/ADD 内存事务时发生系统错误，未写出文件：{exc}",
                category=ValidationCategory.SYSTEM,
                file_path=baseline,
                cause=exc,
            ),),
        )
    check("after_plan")
    return PreparedTransaction(validation, plan, document)


def generate_inputs(
    config_path: str | Path,
    baseline_path: str | Path,
    output_path: str | Path,
    overwrite: bool = False,
) -> GenerationReport:
    """在可丢弃工作树上执行 DELETE→ADD 事务并生成新版完整 ARXML。"""
    baseline = Path(baseline_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    output_issues: list[ValidationIssue] = []
    if output == baseline:
        output_issues.append(_generation_issue(
            "OUTPUT_EQUALS_BASELINE",
            f"输出路径“{output}”不能与输入基准文件相同，请选择新的输出文件。", output,
        ))
    if output.exists() and not overwrite:
        output_issues.append(_generation_issue(
            "OUTPUT_EXISTS",
            f"输出文件“{output}”已存在；默认不覆盖，请更换路径或显式允许覆盖。", output,
        ))

    if output_issues:
        return GenerationReport(
            validation=ValidationReport(tuple(output_issues)), output_path=output,
        )
    prepared = prepare_transaction(config_path, baseline_path)
    plan = prepared.plan
    document = prepared.document
    if prepared.issues or plan is None or document is None or plan.errors:
        return GenerationReport(
            validation=prepared.validation, plan=plan, output_path=output, issues=prepared.issues,
        )

    try:
        document.write_atomic(
            output,
            overwrite=overwrite,
            validator=lambda generated: validate_generated_output(generated, plan),
        )
    except OutputValidationError as exc:
        return GenerationReport(
            validation=prepared.validation, plan=plan, output_path=output,
            issues=(_generation_issue("OUTPUT_VALIDATION_FAILED", str(exc), output),),
        )
    except OutputWriteError as exc:
        cause = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
        return GenerationReport(
            validation=prepared.validation, plan=plan, output_path=output,
            issues=(ValidationIssue(
                code="OUTPUT_WRITE_FAILED", message=str(exc), category=ValidationCategory.SYSTEM,
                file_path=output, cause=cause,
            ),),
        )
    return GenerationReport(
        validation=prepared.validation,
        plan=plan,
        output_path=output,
        output_written=True,
        output_validated=True,
    )
