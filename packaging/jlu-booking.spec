# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


project_dir = Path(SPECPATH).parent

a = Analysis(
    [str(project_dir / "jlu_booking" / "__main__.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[(str(project_dir / "assets" / "JLU_LOGO.png"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JLU Booking",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

bundle_files = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="JLU Booking",
)

if sys.platform == "darwin":
    app = BUNDLE(
        bundle_files,
        name="JLU Booking.app",
        bundle_identifier="io.github.xiaoyoumi9924.jlu-booking",
    )
