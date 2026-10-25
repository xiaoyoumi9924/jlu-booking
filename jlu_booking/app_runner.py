"""Small, testable helpers used by the desktop application's process runner."""

from __future__ import annotations

import sys
from pathlib import Path


PACKAGED_WORKER_NAME = "JLU Booking Worker.exe"


def _packaged_worker_executable(executable: str) -> str:
    """Use the console helper executable when the bundle provides one.

    The main Windows build is a windowed executable, so its child processes
    have no usable stdout/stderr.  A sibling console executable lets the
    booking worker keep streaming its progress back to the task page.
    """

    candidate = Path(executable).with_name(PACKAGED_WORKER_NAME)
    try:
        if candidate.is_file():
            return str(candidate)
    except OSError:
        pass
    return executable


def build_auto_worker_command(
    *,
    real_booking_enabled: bool,
    immediate: bool = False,
    executable: str | None = None,
    frozen: bool | None = None,
    module: str = "jlu_booking.auto",
) -> list[str]:
    """Build the automatic-booking child command for source and packaged apps."""

    python_executable = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        python_executable = _packaged_worker_executable(python_executable)
    command = (
        [python_executable, "--auto-worker"]
        if is_frozen
        else [python_executable, "-m", module]
    )
    if not real_booking_enabled:
        command.append("--dry-run")
    if immediate:
        command.append("--immediate")
    return command
