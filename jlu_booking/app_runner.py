"""Small, testable helpers used by the desktop application's process runner."""

from __future__ import annotations

import sys


def build_auto_worker_command(
    *,
    real_booking_enabled: bool,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """Build the automatic-booking child command for source and packaged apps."""

    python_executable = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    command = (
        [python_executable, "--auto-worker"]
        if is_frozen
        else [python_executable, "-m", "jlu_booking.auto"]
    )
    if not real_booking_enabled:
        command.append("--dry-run")
    return command
