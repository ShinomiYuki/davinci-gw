# 本地发布目录

运行 `scripts/build_mcp_release.ps1` 后，本目录会保留 Windows x64 `onedir` 暂存目录、发布 ZIP 和 ZIP SHA-256。ZIP 内包含 EXE、共享 `_internal`、安装/卸载脚本、版本、构建 commit 和全目录校验和。二进制、暂存目录和校验和由 `.gitignore` 排除；本说明文件进入源码版本控制。
