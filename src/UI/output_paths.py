"""输出文件名建议与提交前的轻量路径检查。"""

from __future__ import annotations

from pathlib import Path


def _available_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        alternative = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not alternative.exists():
            return alternative
    raise ValueError("无法为输出文件生成可用名称，请手动选择其他目录。")


def suggest_output_path(baseline_path: str, target_version: str | None = None) -> str:
    """在基线目录生成不覆盖现有文件的建议路径。"""
    baseline = Path(baseline_path).expanduser()
    suffix = f"_v{target_version}" if target_version else "_new"
    return str(_available_path(baseline.with_name(f"{baseline.stem}{suffix}.arxml")))


def validate_output_path(output_path: str, baseline_path: str) -> str | None:
    """返回面向用户的路径错误；有效时返回 None。"""
    if not output_path.strip():
        return "请选择输出 ARXML 路径。"
    output = Path(output_path).expanduser()
    baseline = Path(baseline_path).expanduser()
    if output.suffix.lower() != ".arxml":
        return "输出文件必须使用 .arxml 扩展名。"
    try:
        if output.resolve() == baseline.resolve():
            return "输出路径不能与基准 ARXML 相同。"
    except OSError:
        pass
    if output.exists():
        return "输出文件已存在，请选择新文件名；工具不会覆盖现有文件。"
    if not output.parent.is_dir():
        return "输出目录不存在，请先选择有效目录。"
    return None
