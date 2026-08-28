"""MCP 边界的 Windows 本地路径安全策略。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_INVALID_CHARS = set('<>"|?*')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class PathPolicyError(ValueError):
    """可安全公开的路径契约错误。"""

    code: str
    message: str
    field_name: str

    def __str__(self) -> str:
        return self.message


def _reject_unsafe_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PathPolicyError("PATH_REQUIRED", "必须提供非空路径。", field_name)
    raw = value.strip()
    if "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise PathPolicyError("PATH_INVALID", "路径包含无效控制字符。", field_name)
    normalized_prefix = raw.replace("/", "\\").lower()
    if (
        _URL.match(raw)
        or normalized_prefix.startswith("\\\\")
        or normalized_prefix.startswith("\\?\\")
        or normalized_prefix.startswith("\\.\\")
        or normalized_prefix.startswith("\\device\\")
        or normalized_prefix.startswith("\\globalroot\\")
    ):
        raise PathPolicyError(
            "LOCAL_PATH_REQUIRED", "仅允许带盘符的本地 Windows 绝对路径；禁止 URL、UNC、设备和命名管道路径。",
            field_name,
        )
    if not _DRIVE_ABSOLUTE.match(raw):
        raise PathPolicyError("ABSOLUTE_PATH_REQUIRED", "必须使用带盘符的本地 Windows 绝对路径。", field_name)
    remainder = raw[2:]
    if ":" in remainder:
        raise PathPolicyError("PATH_INVALID", "路径不允许备用数据流或额外冒号。", field_name)
    for part in re.split(r"[\\/]", remainder):
        if not part:
            continue
        if any(char in _INVALID_CHARS for char in part) or part.endswith((" ", ".")):
            raise PathPolicyError("PATH_INVALID", "路径包含 Windows 不允许的名称字符。", field_name)
        if part.split(".", 1)[0].upper() in _RESERVED_NAMES:
            raise PathPolicyError("DEVICE_PATH_FORBIDDEN", "路径包含 Windows 保留设备名称。", field_name)
    return raw


def validate_input_file(value: object, field_name: str, suffix: str) -> str:
    """返回存在、可读且后缀正确的规范本地文件路径。"""
    raw = _reject_unsafe_text(value, field_name)
    path = Path(raw)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PathPolicyError("INPUT_NOT_FOUND", "输入文件不存在或无法访问。", field_name) from None
    if not resolved.is_file():
        raise PathPolicyError("INPUT_NOT_FILE", "输入路径必须指向普通文件。", field_name)
    if resolved.suffix.casefold() != suffix.casefold():
        raise PathPolicyError("INPUT_TYPE_INVALID", f"输入文件必须使用 {suffix} 后缀。", field_name)
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError:
        raise PathPolicyError("INPUT_NOT_READABLE", "输入文件不可读。", field_name) from None
    return str(resolved)


def validate_new_output(value: object, baseline_path: str, field_name: str = "output_path") -> str:
    """返回父目录已存在、目标尚不存在且不等于基准文件的新输出路径。"""
    raw = _reject_unsafe_text(value, field_name)
    path = Path(raw)
    if path.suffix.casefold() != ".arxml":
        raise PathPolicyError("OUTPUT_TYPE_INVALID", "输出文件必须使用 .arxml 后缀。", field_name)
    try:
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PathPolicyError("OUTPUT_PARENT_MISSING", "输出目录必须已经存在。", field_name) from None
    if not parent.is_dir():
        raise PathPolicyError("OUTPUT_PARENT_INVALID", "输出路径的父级必须是目录。", field_name)
    resolved = parent / path.name
    baseline = Path(baseline_path).resolve(strict=True)
    output_key = os.path.normcase(os.path.realpath(str(resolved)))
    baseline_key = os.path.normcase(os.path.realpath(str(baseline)))
    if output_key == baseline_key:
        raise PathPolicyError("BASELINE_OVERWRITE_FORBIDDEN", "输出不得覆盖或别名指向基准 ARXML。", field_name)
    if resolved.exists():
        try:
            same_as_baseline = os.path.samefile(resolved, baseline)
        except OSError:
            same_as_baseline = False
        if same_as_baseline:
            raise PathPolicyError("BASELINE_OVERWRITE_FORBIDDEN", "输出不得覆盖或别名指向基准 ARXML。", field_name)
        raise PathPolicyError("OUTPUT_EXISTS", "输出文件必须是尚不存在的新文件；禁止覆盖。", field_name)
    return str(resolved)
