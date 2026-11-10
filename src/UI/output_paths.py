"""输出文件名建议与提交前的轻量路径检查。"""

from __future__ import annotations

import os
from pathlib import Path


def _available_path(candidate: Path) -> Path:
    # 用 ``lexists`` 而不是 ``exists``：悬空的符号链接在 ``exists`` 看来是
    # 「不存在」，但这个名字其实已经被占住，真正写文件时会被拒绝。
    if not os.path.lexists(str(candidate)):
        return candidate
    for index in range(2, 10_000):
        alternative = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not os.path.lexists(str(alternative)):
            return alternative
    raise ValueError("无法为输出文件生成可用名称，请手动选择其他目录。")


def suggest_output_path(baseline_path: str, target_version: str | None = None) -> str:
    """在基线目录生成不覆盖现有文件的建议路径。

    基线路径为空或没有文件名时返回空字符串，调用方应据此跳过建议，
    避免在界面事件处理里抛出异常。
    """
    baseline = Path(baseline_path).expanduser()
    if not baseline.name:
        return ""
    suffix = f"_v{target_version}" if target_version else "_new"
    return str(_available_path(baseline.with_name(f"{baseline.stem}{suffix}.arxml")))


def validate_output_path(output_path: str, baseline_path: str) -> str | None:
    """返回面向用户的路径错误；有效时返回 None。"""
    if not output_path.strip():
        return "请选择输出 ARXML 路径。"
    if "\x00" in output_path:
        # 含空字符的路径在 Windows 上无法解析，Path 会把它当成根目录，
        # 校验会误判为"可用"，真正失败要等到生成阶段才暴露。
        return "输出路径包含非法字符，请重新选择。"
    output = Path(output_path).expanduser()
    baseline = Path(baseline_path).expanduser()
    if output.suffix.lower() != ".arxml":
        return "输出文件必须使用 .arxml 扩展名。"
    try:
        if output.resolve() == baseline.resolve():
            return "输出路径不能与基准 ARXML 相同。"
    except (OSError, ValueError):
        # 路径里含空字符等非法字符时 resolve() 抛 ValueError；
        # 这属于用户填错路径，要给出可读提示而不是让异常逃出界面事件处理。
        pass
    if os.path.lexists(str(output)):
        return "输出文件已存在，请选择新文件名；工具不会覆盖现有文件。"
    if not output.parent.is_dir():
        return "输出目录不存在，请先选择有效目录。"
    return None
