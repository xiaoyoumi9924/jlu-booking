import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_repository_privacy_check_passes():
    result = subprocess.run(
        [sys.executable, "tools/privacy_check.py"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (result.stdout + result.stderr).isascii()
