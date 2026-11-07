# -*- mode: python ; coding: utf-8 -*-
import json
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
release = json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))
datas = [(str(ROOT / 'release.json'), '.'),
         (str(ROOT / 'resources/desktop_update_trust.json'), 'resources'),
         (str(ROOT / 'resources/desktop_baselines'), 'resources/desktop_baselines')]
metadata = os.environ.get('LJQC_UPDATE_BUILD_INFO')
if metadata:
    datas.append((metadata, '.'))
a = Analysis([str(ROOT / 'desktop_updates/launcher.py')], pathex=[str(ROOT)],
    datas=datas, binaries=[],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
                   'cryptography.hazmat.primitives.asymmetric.ed25519'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'streamlit', 'pandas', 'matplotlib', 'numpy'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8=1', None, 'OPTION')], exclude_binaries=True,
    name='LJQCAppUpdater', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, argv_emulation=False,
    icon=str(ROOT / 'assets/app_icon.ico') if sys.platform == 'win32' else None,
    target_arch='arm64' if sys.platform == 'darwin' else None,
    codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LJQCAppUpdater')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='LJQCAppUpdater.app', icon=os.environ.get('LJQC_MAC_ICON'),
        bundle_identifier='com.ljqcapp.desktop', version=release['version'], info_plist={
            'CFBundleShortVersionString': release['version'],
            'CFBundleVersion': str(release['build_number']),
            'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
            'NSHumanReadableCopyright': 'LJQCApp'})
