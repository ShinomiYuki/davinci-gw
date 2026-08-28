"""PyInstaller 入口；保持控制台 stdin/stdout 供 MCP STDIO 使用。"""

from davinci_gw.mcp.__main__ import main


if __name__ == "__main__":
    main()
