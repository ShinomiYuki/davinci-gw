"""`python -m davinci_gw.mcp` 与独立 EXE 的唯一入口。"""

from __future__ import annotations

import logging

from davinci_gw.mcp.logging_config import configure_logging
from davinci_gw.mcp.server import build_server


def main() -> None:
    """只启动 STDIO；绝不开放网络传输或监听端口。"""
    configure_logging()
    try:
        build_server().run(transport="stdio")
    except (BrokenPipeError, EOFError):
        logging.getLogger("davinci_gw.mcp").info("stdio peer closed")
    except KeyboardInterrupt:
        logging.getLogger("davinci_gw.mcp").info("stdio server interrupted")


if __name__ == "__main__":
    main()
