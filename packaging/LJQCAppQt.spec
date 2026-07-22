# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve().parent
APP_NAME = os.environ.get("LJQCAPP_QT_APP_NAME", "LJQCApp").strip() or "LJQCApp"
APP_ICON = str(PROJECT_ROOT / "assets" / "app_icon.ico")

qt_datas = (
    collect_data_files("PySide6")
    + collect_data_files("PySide6_Addons")
    + collect_data_files("PySide6_Essentials")
)
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWidgets",
]

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "desktop_qt_launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=qt_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQtWebEngine"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=APP_ICON,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
