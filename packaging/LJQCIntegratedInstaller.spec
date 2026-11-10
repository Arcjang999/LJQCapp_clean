# -*- mode: python ; coding: utf-8 -*-
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
bundle = Path(os.environ['LJQC_INTEGRATED_BUNDLE']).resolve(strict=True)
configuration_raw = (bundle / 'bundle.json').read_bytes()
configuration = json.loads(configuration_raw.decode('utf-8'))
binding_directory = Path(tempfile.mkdtemp(prefix='ljqc-integrated-binding-'))
(binding_directory / 'ljqc_integrated_binding.py').write_text(
    'BUNDLE_SHA256 = ' + repr(hashlib.sha256(configuration_raw).hexdigest()) + '\n', encoding='utf-8')
datas = [(str(ROOT / 'release.json'), '.'),
         (str(ROOT / 'resources/desktop_update_trust.json'), 'resources'),
         (str(ROOT / 'resources/desktop_baselines'), 'resources/desktop_baselines')]
datas += collect_data_files('certifi', includes=['cacert.pem'])
metadata = os.environ.get('LJQC_UPDATE_BUILD_INFO')
if metadata:
    datas.append((metadata, '.'))
# The builder copies the entire bundle after freezing and before outer signing.
# Letting PyInstaller rearrange an embedded .app would break its original seal.
a = Analysis([str(ROOT / 'packaging/integrated_installer_entry.py')],
    pathex=[str(ROOT), str(binding_directory)], datas=datas, binaries=[],
    hiddenimports=['ljqc_integrated_binding', 'PySide6.QtCore', 'PySide6.QtGui',
                   'PySide6.QtWidgets', 'cryptography.hazmat.primitives.asymmetric.ed25519', 'certifi'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'streamlit', 'pandas', 'matplotlib', 'numpy'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8=1', None, 'OPTION')], exclude_binaries=True,
    name='LJQCAppInstaller', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, argv_emulation=False,
    icon=str(ROOT / 'assets/app_icon.ico') if sys.platform == 'win32' else None,
    target_arch='arm64' if sys.platform == 'darwin' else None,
    codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LJQCAppInstaller')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='LJQCAppInstaller.app', icon=os.environ.get('LJQC_MAC_ICON'),
        bundle_identifier='com.ljqcapp.installer', version=configuration['version'], info_plist={
            'CFBundleShortVersionString': configuration['version'],
            'CFBundleVersion': str(configuration['installer_build_number']),
            'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
            'NSHumanReadableCopyright': 'LJQCApp'})
