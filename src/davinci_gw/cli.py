"""第01轮中文命令行入口，仅公开 validate 和 preview。"""

from __future__ import annotations

import argparse
import traceback
from collections.abc import Sequence
from pathlib import Path

from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.validate import validate_inputs
from davinci_gw.domain.models import PreviewReport, ValidationCategory, ValidationReport

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
        print("当前开发轮次尚未执行路由写入。")
    print(f"摘要：错误 {len(report.errors)}，警告 {len(report.warnings)}。")


def _render_preview(preview: PreviewReport) -> None:
    """渲染固定结构的预览，同时明确本轮不会写路由。"""
    if not preview.is_valid:
        _render_issues(preview.validation)
        print(f"摘要：错误 {len(preview.validation.errors)}，警告 {len(preview.validation.warnings)}。")
        return
    inspection = preview.validation.arxml_inspection
    print(f"目标版本：{preview.target_version}\n")
    print("直接报文路由：")
    print(f"  新增：{preview.direct_add_count}")
    print(f"  删除：{preview.direct_delete_count}\n")
    print("信号路由：")
    print(f"  新增：{preview.signal_add_count}")
    print(f"  删除：{preview.signal_delete_count}\n")
    print("引用数据：")
    print(f"  CAN通道：{preview.reference_count}\n")
    print("基准ARXML：")
    print(f"  AUTOSAR Schema：{inspection.schema_filename if inspection else '未识别'}")
    for name in ("CanIf", "Com", "EcuC", "PduR"):
        print(f"  {name}：{'已找到' if inspection and name in inspection.modules else '未找到'}")
    print("\n本轮状态：")
    print("  输入契约和ARXML基础检查已通过。")
    print("  当前开发轮次尚未执行路由写入。")


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
