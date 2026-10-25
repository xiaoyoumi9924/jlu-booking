"""Standard-stream safety for console-free desktop builds.

PyInstaller's windowed/no-console bootloader (and ``pythonw.exe``) sets
``sys.stdin``, ``sys.stdout`` and ``sys.stderr`` to ``None`` on Windows.
Calling ``print()`` or any third-party code that writes to those objects then
raises ``AttributeError`` before the GUI has a chance to appear.  The desktop
entry point calls :func:`ensure_standard_streams` first, so a packaged Windows
build keeps running even though it deliberately has no console.
"""

from __future__ import annotations

import sys


class NullStream:
    """A tiny, writable stand-in for a missing text stream.

    It intentionally does not keep any output.  Desktop builds write their
    diagnostics to the rotating log file instead of a console.
    """

    encoding = "utf-8"
    errors = "replace"
    newlines = None
    mode = "w"

    def write(self, text: object) -> int:
        return len(str(text))

    def writelines(self, lines) -> None:
        for _line in lines:
            pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("当前构建没有可用的控制台")

    @property
    def closed(self) -> bool:
        return False


def _stream_is_usable(stream) -> bool:
    if stream is None:
        return False
    try:
        stream.write("")
        stream.flush()
    except Exception:
        return False
    return True


def ensure_standard_streams() -> None:
    """Replace unusable ``stdout``/``stderr`` objects with safe stand-ins.

    This is a no-op in normal terminal runs, source launches and test runners.
    It also forces UTF-8 output when the stream supports ``reconfigure`` so
    Chinese log messages cannot fail on a legacy Windows code page.
    """

    if not _stream_is_usable(sys.stdout):
        sys.stdout = NullStream()
    if not _stream_is_usable(sys.stderr):
        sys.stderr = NullStream()

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace", line_buffering=True)
        except (OSError, ValueError):
            pass


__all__ = ["NullStream", "ensure_standard_streams"]
