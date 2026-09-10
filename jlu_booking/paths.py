"""Cross-platform locations for user configuration and runtime data."""

import os
from pathlib import Path

from platformdirs import user_config_path, user_state_path


APP_NAME = "jlu-booking"
PROJECT_DIR = Path(__file__).resolve().parent.parent
LEGACY_CONFIG_FILE = PROJECT_DIR / "config" / "auto_booking.json"
LEGACY_RUNTIME_DIR = PROJECT_DIR / "runtime"
USER_CONFIG_DIR = Path(user_config_path(APP_NAME))


def _path_from_env(name):
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def default_config_file():
    """Return the config path while preserving existing source-checkout setups."""

    override = _path_from_env("JLU_BOOKING_CONFIG_FILE")
    if override is not None:
        return override
    if LEGACY_CONFIG_FILE.exists():
        return LEGACY_CONFIG_FILE
    return USER_CONFIG_DIR / "auto_booking.json"


def default_runtime_dir():
    """Return a writable state directory for logs and booking markers."""

    override = _path_from_env("JLU_BOOKING_RUNTIME_DIR")
    if override is not None:
        return override
    if LEGACY_RUNTIME_DIR.exists():
        return LEGACY_RUNTIME_DIR
    return Path(user_state_path(APP_NAME))


AUTO_CONFIG_FILE = default_config_file()
TOKEN_FILE = USER_CONFIG_DIR / "token"
RUNTIME_DIR = default_runtime_dir()
STATE_DIR = RUNTIME_DIR / "state"
LOG_DIR = RUNTIME_DIR / "logs"
