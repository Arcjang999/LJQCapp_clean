# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
from mac_resources import application_datas

datas = application_datas(ROOT) + collect_data_files('streamlit') + copy_metadata('streamlit') + collect_data_files('matplotlib')
datas.append((os.environ['LJQC_BUILD_INFO'], '.'))
hidden = list(dict.fromkeys(sum((collect_submodules(name) for name in ('streamlit', 'pyarrow', 'altair', 'pages', 'migrations', 'services', 'ui')), []) + [
    'database', 'import_review', 'plotting', 'qc_logic', 'zscore_logic', 'zscore_plotting',
    'matplotlib.backends.backend_agg', 'numpy', 'pandas']))
a = Analysis([str(ROOT / 'run_app.py')], pathex=[str(ROOT)], binaries=[], datas=datas,
    hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['PyQt5', 'PyQt6'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LJQCAppService', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True, argv_emulation=False,
    target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LJQCAppService')
