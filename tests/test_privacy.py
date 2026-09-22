import subprocess
import sys
from pathlib import Path

from tools import privacy_check


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


def test_deployment_examples_contain_no_real_secret_or_host():
    text = "\n".join(
        (PROJECT_DIR / path).read_text(encoding="utf-8")
        for path in (
            "deploy/Caddyfile.example",
            "deploy/jlu-booking-web.env.example",
            "deploy/jlu-booking-web.service",
            "deploy/jlu-booking-scheduler.service",
        )
    )
    assert "booking.example.com" in text
    assert "JLU_BOOKING_TOKEN=" not in text
    assert "/Users/" not in text
    assert "private-token" not in text


def test_web_private_artifacts_are_gitignored():
    content = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "*.sqlite3",
        "*.sqlite3-*",
        "*.key",
        ".env",
        "runtime/web/",
        "backups/",
    ):
        assert pattern in content


def test_privacy_path_classifier_rejects_web_runtime_artifacts():
    for path in (
        "data/web.sqlite3",
        "data/web.sqlite3-wal",
        "secrets/token.key",
        "backups/2026-09-22.sqlite3",
        "runtime/web/user-1/task-2/worker.log",
        "deploy/.env",
    ):
        assert privacy_check.is_private_path(path), path
    assert not privacy_check.is_private_path("deploy/jlu-booking-web.env.example")
