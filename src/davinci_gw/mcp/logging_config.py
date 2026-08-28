"""不接触 stdout 的本地滚动日志配置。"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> None:
    """优先写用户本地滚动文件；目录不可用时仅回退到 stderr。"""
    logger = logging.getLogger("davinci_gw.mcp")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    configured = False
    try:
        configured_root = os.environ.get("DAVINCI_GW_LOG_DIR")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not configured_root and not local_app_data:
            raise OSError("local application data directory unavailable")
        log_dir = Path(configured_root) if configured_root else Path(local_app_data) / "DaVinciGW" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "mcp.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        configured = True
    except Exception:
        configured = False
    if not configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.propagate = False
