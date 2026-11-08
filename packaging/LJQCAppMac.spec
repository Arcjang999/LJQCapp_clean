# -*- mode: python ; coding: utf-8 -*-
import json
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
ROOT = Path(SPECPATH).resolve().parent
release = json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))
a = Analysis([str(ROOT / 'packaging/desktop_qt_launcher.py')], pathex=[str(ROOT)], binaries=[],
    datas=[(str(ROOT / 'release.json'), '.'), (os.environ['LJQC_BUILD_INFO'], '.')]
        + collect_data_files('certifi', includes=['cacert.pem']),
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtNetwork', 'PySide6.QtPrintSupport',
        'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWidgets'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['PyQt5', 'PyQt6'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LJQCApp', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False, argv_emulation=False,
    target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LJQCApp')
app = BUNDLE(coll, name='LJQCApp.app', icon=os.environ.get('LJQC_MAC_ICON'),
    bundle_identifier='com.ljqcapp.desktop', version=release['version'], info_plist={
        'CFBundleShortVersionString': release['version'], 'CFBundleVersion': str(release['build_number']),
        'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
        'NSHumanReadableCopyright': 'LJQCApp',
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}})
