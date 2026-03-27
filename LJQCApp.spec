# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


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
    ["run_app.py"],
    pathex=[],
    binaries=[],
    datas=streamlit_datas
    + streamlit_metadata
    + matplotlib_datas
    + [
        ("app.py", "."),
        ("database.py", "."),
        ("plotting.py", "."),
        ("qc_logic.py", "."),
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
