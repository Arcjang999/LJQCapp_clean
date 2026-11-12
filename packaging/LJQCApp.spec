# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


PROJECT_ROOT = Path(SPECPATH).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APP_NAME = os.environ.get("LJQCAPP_APP_NAME", "LJQCAppService").strip() or "LJQCAppService"
BUNDLE_MODE = os.environ.get("LJQCAPP_BUNDLE_MODE", "onedir").strip().lower() or "onedir"
CONSOLE = os.environ.get("LJQCAPP_CONSOLE", "false").strip().lower() == "true"
APP_ICON = str(PROJECT_ROOT / "assets" / "app_icon.ico")

sys.path.insert(0, str(PROJECT_ROOT / "packaging"))
from protected_runtime import application_datas, assert_native_analysis, protected_runtime

runtime_root, protection = protected_runtime(PROJECT_ROOT)
# Compiled modules must win over the development checkout on every import.
sys.path.insert(0, str(runtime_root))
project_source_datas = application_datas(PROJECT_ROOT, runtime_root)
metadata = os.environ.get("LJQCAPP_BUILD_METADATA", "")
if metadata:
    project_source_datas.append((metadata, "."))
streamlit_datas = collect_data_files("streamlit")
matplotlib_datas = collect_data_files("matplotlib")
certifi_datas = collect_data_files("certifi", includes=["cacert.pem"])
streamlit_metadata = copy_metadata("streamlit")
hiddenimports = list(
    dict.fromkeys(
        collect_submodules("streamlit")
        + collect_submodules("pyarrow")
        + collect_submodules("altair")
        + protection["hiddenimports"]
        + [
            "database",
            "import_review",
            "plotting",
            "qc_logic",
            "zscore_logic",
            "zscore_plotting",
            "matplotlib.backends.backend_agg",
            "numpy",
            "pandas",
        ]
    )
)

a = Analysis(
    [str(runtime_root / "service_entry.py")],
    pathex=[
        str(runtime_root),
    ],
    binaries=[],
    datas=streamlit_datas + streamlit_metadata + matplotlib_datas + certifi_datas + project_source_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
assert_native_analysis(a, protection)
pyz = PYZ(a.pure)

if BUNDLE_MODE == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [("X utf8=1", None, "OPTION")],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=CONSOLE,
        icon=APP_ICON,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [("X utf8=1", None, "OPTION")],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=CONSOLE,
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
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )
