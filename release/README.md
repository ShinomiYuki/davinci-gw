# 本地发布目录

运行 `scripts/build_mcp_release.ps1` 后，本目录会保留 Windows x64 `onedir` 暂存目录、发布 ZIP 和 ZIP SHA-256。ZIP 内包含 EXE、共享 `_internal`、安装/卸载脚本、版本、构建 commit 和全目录校验和。

运行 `scripts/build_gui_release.ps1` 后，同一目录下会多出 `davinci-gw-gui-<版本>-win-x64` 暂存目录与对应 ZIP：GUI 包是免安装桌面程序（`davinci-gw-gui.exe`），不需要 Python 或 PATH，目录内 `_internal` 是运行所需文件，不能只复制 EXE。

二进制、暂存目录和校验和由 `.gitignore` 排除；本说明文件进入源码版本控制。
