"""编排第02轮 ADD 规划、应用、输出验证和原子写出。"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.domain.errors import (
    ArxmlStructureError,
    OutputValidationError,
    OutputWriteError,
)
from davinci_gw.domain.models import (
    GenerationReport,
    OperationType,
    ValidationCategory,
    ValidationIssue,
    ValidationReport,
)
from davinci_gw.input.workbook_reader import read_workbook
from davinci_gw.routing.add import AddCoordinator
from davinci_gw.validation.output import validate_generated_output

from .validate import _missing_file, _system_issue


def _generation_issue(code: str, message: str, path: Path | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, file_path=path)


def generate_inputs(
    config_path: str | Path,
    baseline_path: str | Path,
    output_path: str | Path,
    overwrite: bool = False,
) -> GenerationReport:
    """生成新版完整 ARXML；单条缺失可跳过，DELETE和冲突仍阻止正式输出。"""
    config = Path(config_path).expanduser().resolve()
    baseline = Path(baseline_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    issues: list[ValidationIssue] = []
    workbook_data = None
    inspection = None
    document = None

    if not config.is_file():
        issues.append(_missing_file(config, "配置表"))
    else:
        try:
            workbook_result = read_workbook(config)
            workbook_data = workbook_result.data
            issues.extend(workbook_result.issues)
        except Exception as exc:
            issues.append(_system_issue(config, "配置表", "无法读取", exc))

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

    if output == baseline:
        issues.append(_generation_issue(
            "OUTPUT_EQUALS_BASELINE",
            f"输出路径“{output}”不能与输入基准文件相同，请选择新的输出文件。", output,
        ))
    if output.exists() and not overwrite:
        issues.append(_generation_issue(
            "OUTPUT_EXISTS",
            f"输出文件“{output}”已存在；默认不覆盖，请更换路径或显式允许覆盖。", output,
        ))

    validation = ValidationReport(tuple(issues), workbook_data, inspection)
    if not validation.is_valid or workbook_data is None or document is None:
        return GenerationReport(validation=validation, output_path=output)

    direct_deletes = sum(route.operation is OperationType.DELETE for route in workbook_data.direct_routes)
    signal_deletes = sum(route.operation is OperationType.DELETE for route in workbook_data.signal_routes)
    if direct_deletes or signal_deletes:
        delete_issue = _generation_issue(
            "DELETE_NOT_SUPPORTED",
            f"当前版本仅实现ADD，配置表包含尚未支持的DELETE：直接报文{direct_deletes}条，"
            f"信号路由{signal_deletes}条。为避免生成不完整目标版本，本次未写输出；"
            "请等待DELETE功能实现或使用不含DELETE的正式配置表。",
            config,
        )
        return GenerationReport(
            validation=validation, output_path=output, issues=(delete_issue,),
        )

    try:
        coordinator = AddCoordinator(document, workbook_data)
        plan = coordinator.plan()
    except ArxmlStructureError as exc:
        return GenerationReport(
            validation=validation,
            output_path=output,
            issues=(_generation_issue("ARXML_ADD_STRUCTURE_INVALID", str(exc), baseline),),
        )
    if plan.errors:
        return GenerationReport(validation=validation, plan=plan, output_path=output)

    try:
        coordinator.apply(plan)
    except ArxmlStructureError as exc:
        return GenerationReport(
            validation=validation, plan=plan, output_path=output,
            issues=(_generation_issue(
                "ARXML_ADD_APPLY_FAILED",
                f"应用ADD计划失败，未写出文件：{exc}", baseline,
            ),),
        )
    except Exception as exc:
        return GenerationReport(
            validation=validation, plan=plan, output_path=output,
            issues=(ValidationIssue(
                code="ARXML_ADD_SYSTEM_FAILED",
                message=f"应用ADD计划时发生系统错误，未写出文件：{exc}",
                category=ValidationCategory.SYSTEM,
                file_path=baseline,
                cause=exc,
            ),),
        )

    try:
        document.write_atomic(
            output,
            overwrite=overwrite,
            validator=lambda generated: validate_generated_output(generated, plan),
        )
    except OutputValidationError as exc:
        return GenerationReport(
            validation=validation, plan=plan, output_path=output,
            issues=(_generation_issue("OUTPUT_VALIDATION_FAILED", str(exc), output),),
        )
    except OutputWriteError as exc:
        cause = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
        return GenerationReport(
            validation=validation, plan=plan, output_path=output,
            issues=(ValidationIssue(
                code="OUTPUT_WRITE_FAILED", message=str(exc), category=ValidationCategory.SYSTEM,
                file_path=output, cause=cause,
            ),),
        )
    return GenerationReport(
        validation=validation,
        plan=plan,
        output_path=output,
        output_written=True,
        output_validated=True,
    )
