"""Build the current platform's standalone desktop package with PyInstaller."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_DIR / "packaging" / "jlu-booking.spec"


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(SPEC_FILE),
    ]
    try:
        subprocess.run(command, cwd=PROJECT_DIR, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    print(f"Package build completed: {PROJECT_DIR / 'dist'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
