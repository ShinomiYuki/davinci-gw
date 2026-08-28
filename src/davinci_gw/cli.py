"""中文命令行入口，提供 validate、事务 preview 和第03轮 generate。"""

from __future__ import annotations

import argparse
import traceback
from collections.abc import Sequence
from pathlib import Path

from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.generate import generate_inputs
from davinci_gw.application.validate import validate_inputs
from davinci_gw.domain.models import (
    GenerationReport,
    PreviewReport,
    ValidationCategory,
    ValidationReport,
)

EXIT_OK = 0
EXIT_CONTRACT_ERROR = 2
EXIT_SYSTEM_ERROR = 3


def _parser() -> argparse.ArgumentParser:
    """构造只负责路径和调试开关的 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="davinci-gw",
        description="检查标准网关路由配置表和基准 ARXML 是否可安全用于后续处理。",
    )
    parser.add_argument("--debug", action="store_true", help="发生意外异常时显示完整堆栈")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("validate", "校验配置表契约、目标版本和基准 ARXML"),
        ("preview", "校验并预览 ADD、DELETE 数量，不写入 ARXML"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("--config", type=Path, required=True, help="标准配置表路径")
        child.add_argument("--baseline", type=Path, required=True, help="旧版完整 ARXML 路径")
    generate = subparsers.add_parser("generate", help="应用DELETE与ADD事务并原子写出新版完整ARXML")
    generate.add_argument("--config", type=Path, required=True, help="标准配置表路径")
    generate.add_argument("--baseline", type=Path, required=True, help="旧版完整ARXML路径")
    generate.add_argument("--output", type=Path, required=True, help="新版完整ARXML输出路径")
    generate.add_argument("--overwrite", action="store_true", help="显式允许覆盖已有输出；永不覆盖基线")
    return parser


def _exit_code(report: ValidationReport) -> int:
    if report.is_valid:
        return EXIT_OK
    if any(issue.category is ValidationCategory.SYSTEM for issue in report.errors):
        return EXIT_SYSTEM_ERROR
    return EXIT_CONTRACT_ERROR


def _render_issues(report: ValidationReport) -> None:
    for issue in report.errors:
        print(f"错误：{issue.message}")
    for issue in report.warnings:
        print(f"警告：{issue.message}")


def _render_debug_causes(report: ValidationReport) -> None:
    """调试模式下补充结构化系统问题保留的原始异常堆栈。"""
    for issue in report.issues:
        if issue.cause is not None:
            traceback.print_exception(issue.cause)


def _render_validate(report: ValidationReport) -> None:
    """将结构化校验结果转换为简洁中文输出。"""
    _render_issues(report)
    if report.is_valid:
        print("输入校验通过。")
        if report.workbook_data:
            print(f"目标版本：{report.workbook_data.target_version}")
        if report.arxml_inspection:
            print(f"AUTOSAR Schema：{report.arxml_inspection.schema_filename}")
            print("必要模块：CanIf、Com、EcuC、PduR 均已找到。")
        print("validate 仅校验输入，不执行路由写入。")
    print(f"摘要：错误 {len(report.errors)}，警告 {len(report.warnings)}。")


def _render_preview(preview: PreviewReport) -> None:
    """渲染投影规划的新增、删除、幂等、保留、跳过和冲突状态。"""
    if not preview.is_valid:
        _render_issues(preview.validation)
        print(f"摘要：错误 {len(preview.validation.errors)}，警告 {len(preview.validation.warnings)}。")
        return
    inspection = preview.validation.arxml_inspection
    plan = preview.plan
    print(f"目标版本：{preview.target_version}\n")
    print("直接报文路由：")
    print(f"  新增：{plan.direct_added_count if plan else preview.direct_add_count}")
    print(f"  删除：{plan.direct_deleted_count if plan else preview.direct_delete_count}")
    print(f"  已存在：{plan.direct_existing_count if plan else 0}")
    print(f"  已不存在：{plan.direct_missing_count if plan else 0}")
    print(f"  保留共享对象：{plan.direct_retained_count if plan else 0}")
    print(f"  跳过：{plan.direct_skipped_count if plan else 0}")
    print(f"  冲突：{plan.direct_conflict_count if plan else 0}\n")
    print("信号路由：")
    print(f"  新增：{plan.signal_added_count if plan else preview.signal_add_count}")
    print(f"  删除：{plan.signal_deleted_count if plan else preview.signal_delete_count}")
    print(f"  已存在：{plan.signal_existing_count if plan else 0}")
    print(f"  已不存在：{plan.signal_missing_count if plan else 0}")
    print(f"  保留超时：{plan.signal_timeout_retained_count if plan else 0}")
    print(f"  清除超时：{plan.signal_timeout_removed_count if plan else 0}")
    print(f"  跳过：{plan.signal_skipped_count if plan else 0}")
    print(f"  冲突：{plan.signal_conflict_count if plan else 0}\n")
    print("引用数据：")
    print(f"  CAN通道：{preview.reference_count}\n")
    print("基准ARXML：")
    print(f"  AUTOSAR Schema：{inspection.schema_filename if inspection else '未识别'}")
    for name in ("CanIf", "Com", "EcuC", "PduR"):
        print(f"  {name}：{'已找到' if inspection and name in inspection.modules else '未找到'}")
    print("\n预览状态：完整 DELETE 投影与 ADD 规划已通过；未写入任何文件。")
    if plan and plan.decisions:
        print("\n保留决策：")
        for decision in plan.decisions:
            print(f"  - {decision.object_path}：{decision.reason}")


def _render_generation(report: GenerationReport) -> None:
    """渲染新增、幂等跳过、缺失跳过和输出验证结果。"""
    for issue in report.errors:
        print(f"错误：{issue.message}")
    for issue in report.warnings:
        print(f"警告：{issue.message}")
    if not report.is_success:
        print(f"摘要：错误 {len(report.errors)}，警告 {len(report.warnings)}；未生成输出。")
        return
    plan = report.plan
    data = report.validation.workbook_data
    print(f"目标版本：{data.target_version if data else '未识别'}\n")
    print("直接报文路由：")
    print(f"  新增：{plan.direct_added_count}")
    print(f"  删除：{plan.direct_deleted_count}")
    print(f"  已存在并跳过：{plan.direct_existing_count}")
    print(f"  已不存在并跳过：{plan.direct_missing_count}")
    print(f"  保留共享对象：{plan.direct_retained_count}")
    print(f"  缺失或不支持并跳过：{plan.direct_skipped_count}\n")
    print("信号路由：")
    print(f"  新增：{plan.signal_added_count}")
    print(f"  删除：{plan.signal_deleted_count}")
    print(f"  已存在并跳过：{plan.signal_existing_count}")
    print(f"  已不存在并跳过：{plan.signal_missing_count}")
    print(f"  保留共享对象：{plan.signal_retained_count}")
    print(f"  保留超时：{plan.signal_timeout_retained_count}")
    print(f"  清除超时：{plan.signal_timeout_removed_count}")
    print(f"  缺失或不支持并跳过：{plan.signal_skipped_count}\n")
    if plan.decisions:
        print("保留决策：")
        for decision in plan.decisions:
            print(f"  - {decision.object_path}：{decision.reason}")
        print()
    print(f"输出文件：{report.output_path}")
    print("输出验证：通过")


def main(argv: Sequence[str] | None = None) -> int:
    """执行 CLI 并返回稳定退出码；普通模式不显示 Python Traceback。"""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            report = validate_inputs(args.config, args.baseline)
            _render_validate(report)
            if args.debug:
                _render_debug_causes(report)
            return _exit_code(report)
        if args.command == "generate":
            report = generate_inputs(
                args.config, args.baseline, args.output, overwrite=args.overwrite,
            )
            _render_generation(report)
            if args.debug:
                for issue in report.all_issues:
                    if issue.cause is not None:
                        traceback.print_exception(issue.cause)
            if report.is_success:
                return EXIT_OK
            if any(issue.category is ValidationCategory.SYSTEM for issue in report.errors):
                return EXIT_SYSTEM_ERROR
            return EXIT_CONTRACT_ERROR
        preview = preview_inputs(args.config, args.baseline)
        _render_preview(preview)
        if args.debug:
            _render_debug_causes(preview.validation)
        return _exit_code(preview.validation)
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"错误：程序处理输入时发生未预期错误：{exc}。可使用 --debug 获取诊断信息。")
        return EXIT_SYSTEM_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
