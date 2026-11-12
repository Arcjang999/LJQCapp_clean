# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / 'packaging'))
from protected_runtime import application_modules, assert_native_analysis, imported_modules, protected_runtime
runtime_root, protection = protected_runtime(PROJECT_ROOT)
sys.path.insert(0, str(runtime_root))
launcher_modules = {'services.release_info', 'services.storage_paths'}
launcher_dependencies = imported_modules({name: path for name, path in application_modules(PROJECT_ROOT).items()
                                        if name in launcher_modules})
APP_NAME = os.environ.get("LJQCAPP_QT_APP_NAME", "LJQCApp").strip() or "LJQCApp"
APP_ICON = str(PROJECT_ROOT / "assets" / "app_icon.ico")
release_datas = [(str(PROJECT_ROOT / "release.json"), ".")]
metadata = os.environ.get("LJQCAPP_BUILD_METADATA", "")
if metadata:
    release_datas.append((metadata, "."))

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
] + launcher_dependencies

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "desktop_qt_launcher.py")],
    pathex=[str(runtime_root)],
    binaries=[],
    datas=qt_datas + release_datas + collect_data_files('certifi', includes=['cacert.pem']),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQtWebEngine"],
    noarchive=False,
    optimize=0,
)
assert_native_analysis(a, protection, required_modules=launcher_modules)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8=1", None, "OPTION")],
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
