# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(__file__).resolve().parent.parent

streamlit_datas = collect_data_files("streamlit")
matplotlib_datas = collect_data_files("matplotlib")
streamlit_metadata = copy_metadata("streamlit")
hiddenimports = (
    collect_submodules("streamlit")
    + collect_submodules("pyarrow")
    + collect_submodules("altair")
    + [
        "matplotlib.backends.backend_agg",
        "numpy",
        "pandas",
    ]
)


a = Analysis(
    [str(PROJECT_ROOT / "run_app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=streamlit_datas
    + streamlit_metadata
    + matplotlib_datas
    + [
        (str(PROJECT_ROOT / "app.py"), "."),
        (str(PROJECT_ROOT / "database.py"), "."),
        (str(PROJECT_ROOT / "plotting.py"), "."),
        (str(PROJECT_ROOT / "qc_logic.py"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LJQCApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LJQCApp",
)
