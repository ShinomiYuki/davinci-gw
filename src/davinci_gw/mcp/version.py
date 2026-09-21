"""MCP 独立版本和可追溯构建信息。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MCP_VERSION = "1.1.2"


def _build_info_path() -> Path:
    """同时支持源码运行和 PyInstaller onedir 数据目录。"""
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    candidate = bundled / "davinci_gw" / "mcp" / "BUILD_INFO.json"
    return candidate if candidate.is_file() else Path(__file__).with_name("BUILD_INFO.json")


def load_build_info(repository_path: Path | None = None) -> dict[str, Any]:
    """读取打包时固定的提交；源码运行时仅对显式仓库查询当前提交。"""
    path = _build_info_path()
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    result: dict[str, Any] = {"version": MCP_VERSION, "commit": None, "built_at": None, "dirty": None}
    if repository_path is not None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository_path), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            result["commit"] = completed.stdout.strip()
            status = subprocess.run(
                ["git", "-C", str(repository_path), "status", "--porcelain"],
                check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            result["dirty"] = bool(status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return result
