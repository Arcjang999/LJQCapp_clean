# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
from protected_runtime import application_datas, assert_native_analysis, protected_runtime
runtime_root, protection = protected_runtime(ROOT)
sys.path.insert(0, str(runtime_root))

datas = (application_datas(ROOT, runtime_root) + collect_data_files('streamlit') + copy_metadata('streamlit')
         + collect_data_files('matplotlib') + collect_data_files('certifi', includes=['cacert.pem']))
datas.append((os.environ['LJQC_BUILD_INFO'], '.'))
hidden = list(dict.fromkeys(sum((collect_submodules(name) for name in ('streamlit', 'pyarrow', 'altair')), []) + protection['hiddenimports'] + [
    'database', 'import_review', 'plotting', 'qc_logic', 'zscore_logic', 'zscore_plotting',
    'matplotlib.backends.backend_agg', 'numpy', 'pandas']))
a = Analysis([str(runtime_root / 'service_entry.py')], pathex=[str(runtime_root)], binaries=[], datas=datas,
    hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['PyQt5', 'PyQt6'], noarchive=False, optimize=0)
assert_native_analysis(a, protection)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LJQCAppService', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True, argv_emulation=False,
    target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LJQCAppService')
