# -*- mode: python ; coding: utf-8 -*-
"""DaVinci GW MCP 的 Windows x64 单文件控制台构建。"""

from pathlib import Path

project_root = Path(SPEC).resolve().parents[1]

analysis = Analysis(
    [str(project_root / "packaging" / "mcp_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    analysis.binaries,
    analysis.datas,
    [],
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
