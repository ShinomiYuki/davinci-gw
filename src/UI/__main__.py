"""Windows GUI 入口。"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from . import __version__
from .logging_config import configure_logging
from .main_window import MainWindow
from .styles import APP_STYLE
from .task_runner import QtTaskRunner
from .view_model import GatewayViewModel


def main() -> int:
    """配置高 DPI、日志和单一工作线程后进入 Qt 事件循环。"""
    QCoreApplication.setOrganizationName("DaVinciGW")
    QCoreApplication.setApplicationName("DaVinciGW")
    QCoreApplication.setApplicationVersion(__version__)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    configure_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    runner = QtTaskRunner()
    view_model = GatewayViewModel(runner)
    window = MainWindow(view_model)
    window.show()
    logging.getLogger("davinci_gw.gui").info("GUI 启动 version=%s", __version__)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
