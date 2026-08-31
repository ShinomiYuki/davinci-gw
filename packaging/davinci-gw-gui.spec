# -*- mode: python ; coding: utf-8 -*-
"""DaVinci GW GUI 的 Windows x64 免安装目录构建。"""

from pathlib import Path

project_root = Path(SPEC).resolve().parents[1]

analysis = Analysis(
    [str(project_root / "packaging" / "gui_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.Qt3DCore", "PySide6.QtBluetooth", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtNetwork",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf", "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
        "PySide6.QtSerialPort", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets", "numpy", "pytest", "tkinter",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="davinci-gw-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    version=str(project_root / "packaging" / "gui_version_info.txt"),
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="davinci-gw-gui",
)
