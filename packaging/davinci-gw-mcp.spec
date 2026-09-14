# -*- mode: python ; coding: utf-8 -*-
"""DaVinci GW MCP 的 Windows x64 onedir 控制台构建。"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPEC).resolve().parents[1]
build_info = Path(os.environ["DAVINCI_GW_BUILD_INFO"])
datas = collect_data_files("codex_cli_bin")
datas.append((str(build_info), "davinci_gw/mcp"))

analysis = Analysis(
    [str(project_root / "packaging" / "mcp_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("openai_codex"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="davinci-gw-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    version=str(project_root / "packaging" / "version_info.txt"),
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="davinci-gw-mcp",
)
