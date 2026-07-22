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

PROJECT_MODULE_FILES = [
    "app.py",
    "database.py",
    "import_review.py",
    "plotting.py",
    "qc_logic.py",
    "zscore_logic.py",
    "zscore_plotting.py",
]
PROJECT_PACKAGES = [
    "pages",
    "services",
    "ui",
]


def collect_project_source_datas() -> list[tuple[str, str]]:
    datas: list[tuple[str, str]] = []
    for module_file in PROJECT_MODULE_FILES:
        datas.append((str(PROJECT_ROOT / module_file), "."))

    for package_name in PROJECT_PACKAGES:
        package_root = PROJECT_ROOT / package_name
        for file_path in package_root.rglob("*"):
            if not file_path.is_file():
                continue
            relative_parent = file_path.parent.relative_to(PROJECT_ROOT)
            datas.append((str(file_path), str(relative_parent)))
    return datas


project_source_datas = collect_project_source_datas()
streamlit_datas = collect_data_files("streamlit")
matplotlib_datas = collect_data_files("matplotlib")
streamlit_metadata = copy_metadata("streamlit")
hiddenimports = list(
    dict.fromkeys(
        collect_submodules("streamlit")
        + collect_submodules("pyarrow")
        + collect_submodules("altair")
        + collect_submodules("pages")
        + collect_submodules("services")
        + collect_submodules("ui")
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
    [str(PROJECT_ROOT / "run_app.py")],
    pathex=[
        str(PROJECT_ROOT),
        str(PROJECT_ROOT / "pages"),
        str(PROJECT_ROOT / "ui"),
    ],
    binaries=[],
    datas=streamlit_datas + streamlit_metadata + matplotlib_datas + project_source_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if BUNDLE_MODE == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
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
        [],
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
