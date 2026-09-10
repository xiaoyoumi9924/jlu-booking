"""Fail safely when private runtime data could enter Git or release packages."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
FORBIDDEN_TRACKED_PATHS = {
    "config/auto_booking.json",
    "credentials.json",
    ".env",
}
FORBIDDEN_TRACKED_PREFIXES = ("runtime/", "logs/", "state/", "dist/", "build/")
HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "GitHub Token": re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "OpenAI/API Token": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "JWT": re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
}
PRODUCTION_LITERAL_CREDENTIAL = re.compile(
    r"(?i)\b(?:token|api[_-]?key|secret|password)\s*=\s*['\"]([^'\"]{8,})['\"]"
)


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
    )
    return [PROJECT_DIR / item.decode() for item in result.stdout.split(b"\0") if item]


def check_example_config(errors: list[str]) -> None:
    path = PROJECT_DIR / "config" / "auto_booking.example.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot read example config: {path.relative_to(PROJECT_DIR)} ({exc})")
        return

    sensitive_keys = {
        str(key).lower()
        for key in config
        if any(word in str(key).lower() for word in ("token", "secret", "password"))
    }
    if sensitive_keys:
        errors.append("Example config contains a credential field.")
    if str(config.get("companion_student_number", "")).strip():
        errors.append("Companion student number must be blank in the example config.")
    if config.get("real_booking_enabled") is not False:
        errors.append("Real booking must be disabled in the example config.")


def check_package_spec(errors: list[str]) -> None:
    path = PROJECT_DIR / "packaging" / "jlu-booking.spec"
    try:
        content = path.read_text(encoding="utf-8").lower()
    except OSError as exc:
        errors.append(f"Cannot read package spec: {exc}")
        return

    datas_section = content.split("datas=", 1)[-1].split("hiddenimports=", 1)[0]
    for forbidden in ("config", "runtime", "token", ".env", "credentials"):
        if forbidden in datas_section:
            errors.append(f"Package spec datas must not contain {forbidden!r}.")


def run_checks() -> list[str]:
    errors: list[str] = []
    paths = tracked_paths()
    relative_paths = {path.relative_to(PROJECT_DIR).as_posix() for path in paths}

    for forbidden in sorted(FORBIDDEN_TRACKED_PATHS & relative_paths):
        errors.append(f"Private file is tracked by Git: {forbidden}")
    for relative in sorted(relative_paths):
        if relative.startswith(FORBIDDEN_TRACKED_PREFIXES):
            errors.append(f"Runtime data or build output is tracked by Git: {relative}")

    for path in paths:
        relative = path.relative_to(PROJECT_DIR).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            errors.append(f"Cannot scan {relative}: {exc}")
            continue
        if b"\0" in raw:
            continue
        content = raw.decode("utf-8", errors="replace")
        for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{relative} may contain a {label}; value is not displayed.")
        if relative.startswith("jlu_booking/") and PRODUCTION_LITERAL_CREDENTIAL.search(content):
            errors.append(f"{relative} may contain a hard-coded credential; value is not displayed.")

    check_example_config(errors)
    check_package_spec(errors)
    return errors


def main() -> int:
    try:
        errors = run_checks()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Privacy check could not run: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("Privacy check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Privacy check passed: no private runtime data or high-confidence secrets are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
