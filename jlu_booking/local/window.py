"""Open the local workspace in a native window, with browser fallbacks."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .bootstrap import local_data_dir


WINDOW_TITLE = "JLU Booking · 吉林大学体育场馆"


def _chrome_candidates() -> list[Path]:
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    if sys.platform.startswith("win"):
        roots = [
            Path(value)
            for value in (
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            )
            if value
        ]
        return [
            root / "Google/Chrome/Application/chrome.exe"
            for root in roots
        ] + [
            root / "Microsoft/Edge/Application/msedge.exe"
            for root in roots
        ]
    return [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/microsoft-edge"),
    ]


def _open_chrome_app(url: str) -> bool:
    """Open a standalone Chrome/Edge app window and wait for it to close."""

    executable = next((item for item in _chrome_candidates() if item.is_file()), None)
    if executable is None:
        return False
    profile_dir = local_data_dir() / "window-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            [
                str(executable),
                f"--app={url}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    started = time.monotonic()
    exit_code = process.wait()
    # A second Chrome instance normally hands the window to an existing
    # process and returns immediately; keep the server alive in that case.
    return not (exit_code == 0 and time.monotonic() - started < 2.0)


def open_app_window(*, url: str) -> bool:
    """Show the UI and block until its window closes.

    Returns ``True`` when a window was closed by the user, ``False`` when the
    caller should keep serving and wait for an explicit interrupt instead.
    """

    try:
        import webview
    except Exception:
        webview = None

    if webview is not None:
        try:
            webview.create_window(
                WINDOW_TITLE,
                url,
                width=1440,
                height=900,
                min_size=(1024, 700),
            )
            storage = local_data_dir() / "webview"
            try:
                storage.mkdir(parents=True, exist_ok=True)
            except OSError:
                storage = None
            webview.start(
                private_mode=True,
                storage_path=str(storage) if storage is not None else None,
            )
            return True
        except Exception:
            # A missing native backend should fall back to Chrome, not crash
            # the whole desktop app.
            pass

    if _open_chrome_app(url):
        return True

    webbrowser.open(url)
    return False
