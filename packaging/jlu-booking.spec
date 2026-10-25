# -*- mode: python ; coding: utf-8 -*-

import importlib.util
import sys
from pathlib import Path


project_dir = Path(SPECPATH).parent

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "multipart",
    "multipart.multipart",
    "webview",
]

if sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    hiddenimports += ["webview.platforms.edgechromium"]
elif importlib.util.find_spec("PySide6") is not None:
    hiddenimports += [
        "webview.platforms.qt",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
    ]
else:
    # Linux builds without a Qt backend can still open the browser fallback.
    hiddenimports += ["webview.platforms.gtk"]

a = Analysis(
    [str(project_dir / "jlu_booking" / "__main__.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        (str(project_dir / "assets" / "JLU_LOGO.png"), "assets"),
        (
            str(project_dir / "jlu_booking" / "web" / "templates"),
            "jlu_booking/web/templates",
        ),
        (
            str(project_dir / "jlu_booking" / "web" / "static"),
            "jlu_booking/web/static",
        ),
    ],
    hiddenimports=hiddenimports,
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
