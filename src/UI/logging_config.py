"""GUI 本地轮转日志和未捕获异常入口。"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "DaVinciGW" / "logs" / "gui.log"


def configure_logging() -> Path:
    """创建用户目录日志，不在安装目录写入运行数据。"""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("davinci_gw.gui")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    def exception_hook(exc_type, exc_value, traceback) -> None:
        logger.error("未捕获异常 type=%s", exc_type.__name__)
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = exception_hook
    return path
