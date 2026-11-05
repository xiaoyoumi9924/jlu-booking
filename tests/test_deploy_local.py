"""Offline checks for the local Web deployment tool."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from datetime import time as clock_time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import deploy_local


def make_settings(tmp_path: Path, **overrides) -> deploy_local.Settings:
    values = {
        "venv": tmp_path / "venv",
        "data": tmp_path / "data",
        "host": "127.0.0.1",
        "port": 8123,
        "window_start": "07:25",
        "window_end": "07:35",
        "drain": 300,
    }
    values.update(overrides)
    return deploy_local.Settings(**values)


def test_env_file_round_trip(tmp_path):
    settings = make_settings(tmp_path)

    deploy_local.write_env_file(settings)
    values = deploy_local.read_env_file(settings.env_file)

    assert values["JLU_BOOKING_WEB_DATA_DIR"] == str(settings.data)
    assert values["JLU_BOOKING_WEB_COOKIE_SECURE"] == "false"
    assert values["JLU_BOOKING_LOCAL_PORT"] == "8123"
    assert values["JLU_BOOKING_LOCAL_WINDOW_START"] == "07:25"
    if os.name != "nt":
        assert settings.env_file.stat().st_mode & 0o077 == 0


def test_env_file_tolerates_comments_exports_and_quotes(tmp_path):
    path = tmp_path / "web.env"
    path.write_text(
        "# comment\n"
        "export JLU_BOOKING_LOCAL_PORT=9000\n"
        'JLU_BOOKING_WEB_DATA_DIR="/tmp/with space"\n'
        "BROKEN LINE\n",
        encoding="utf-8",
    )

    values = deploy_local.read_env_file(path)

    assert values["JLU_BOOKING_LOCAL_PORT"] == "9000"
    assert values["JLU_BOOKING_WEB_DATA_DIR"] == "/tmp/with space"
    assert "BROKEN LINE" not in values


def test_app_environment_forces_local_cookie_behaviour(tmp_path):
    settings = make_settings(tmp_path)
    deploy_local.write_env_file(settings)

    environment = deploy_local.app_environment(settings)

    assert environment["JLU_BOOKING_WEB_COOKIE_SECURE"] == "false"
    assert environment["JLU_BOOKING_WEB_DATA_DIR"] == str(settings.data)
    assert environment["JLU_BOOKING_WEB_TOKEN_KEY_FILE"] == str(
        settings.secrets / "token.key"
    )


def test_parse_clock_accepts_both_padded_and_short_hours():
    assert deploy_local.parse_clock("07:25") == clock_time(7, 25)
    assert deploy_local.parse_clock("7:05") == clock_time(7, 5)


@pytest.mark.parametrize(
    "value",
    ["", "7", "25:00", "07:60", "07-25", "07:25:00", "abc"],
)
def test_parse_clock_rejects_bad_values(value):
    with pytest.raises(deploy_local.DeployError):
        deploy_local.parse_clock(value)


def test_validate_window_rejects_inverted_or_equal_hours():
    with pytest.raises(deploy_local.DeployError):
        deploy_local.validate_window("07:35", "07:25")
    with pytest.raises(deploy_local.DeployError):
        deploy_local.validate_window("07:25", "07:25")


def test_launchd_plist_runs_the_daily_window(tmp_path):
    settings = make_settings(tmp_path)
    payload = plistlib.loads(
        deploy_local.build_launchd_plist(
            settings, settings.python, tmp_path / "deploy_local.py"
        )
    )

    assert payload["Label"] == deploy_local.LAUNCHD_LABEL
    assert payload["StartCalendarInterval"] == {"Hour": 7, "Minute": 25}
    assert payload["RunAtLoad"] is False
    arguments = payload["ProgramArguments"]
    assert arguments[-1] == "run-window"
    assert str(settings.data) in arguments
    assert str(settings.venv) in arguments


def test_systemd_units_use_the_configured_window(tmp_path):
    settings = make_settings(tmp_path, window_start="06:40", window_end="06:50")

    units = deploy_local.build_systemd_units(
        settings, Path("/opt/venv/bin/python"), Path("/opt/repo/tools/deploy_local.py")
    )

    timer = units[f"{deploy_local.SYSTEMD_PREFIX}.timer"]
    assert "OnCalendar=*-*-* 06:40:00" in timer
    assert "Persistent=true" in timer
    service = units[f"{deploy_local.SYSTEMD_PREFIX}.service"]
    assert "run-window" in service
    assert "TimeoutStartSec=1800" in service
    # The window itself waits for running tasks, so oneshot must not be killed early.


def test_windows_task_is_daily_and_runs_when_available(tmp_path):
    settings = make_settings(tmp_path)

    xml = deploy_local.build_windows_task_xml(
        settings,
        Path(r"C:\venv\Scripts\pythonw.exe"),
        Path(r"C:\repo\tools\deploy_local.py"),
        r"DOMAIN\student",
    )

    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "T07:25:00" in xml
    assert "<DaysInterval>1</DaysInterval>" in xml
    assert "run-window" in xml
    assert "pythonw.exe" in xml


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock semantics")
def test_scheduler_lock_is_exclusive_and_released(tmp_path):
    settings = make_settings(tmp_path)

    first = deploy_local.acquire_scheduler_lock(settings)
    assert first is not None
    try:
        assert deploy_local.acquire_scheduler_lock(settings) is None
        assert deploy_local.scheduler_active(settings) is True
    finally:
        first.close()

    assert deploy_local.scheduler_active(settings) is False


def test_read_pid_discards_a_stale_file(tmp_path):
    settings = make_settings(tmp_path)
    settings.run_dir.mkdir(parents=True)
    settings.web_pid_file.write_text("999999\n", encoding="utf-8")

    assert deploy_local.read_pid(settings.web_pid_file) is None
    assert not settings.web_pid_file.exists()


def test_read_pid_accepts_the_current_process(tmp_path):
    settings = make_settings(tmp_path)
    settings.run_dir.mkdir(parents=True)
    deploy_local.write_pid(settings.web_pid_file, os.getpid())

    assert deploy_local.read_pid(settings.web_pid_file) == os.getpid()


def test_invite_code_is_read_from_the_auth_module():
    code = deploy_local.invite_code()

    assert code
    assert re.fullmatch(r"\d+", code)

def test_open_delegates_to_up_before_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        deploy_local, "cmd_up", lambda args: calls.append(args) or 0
    )
    args = SimpleNamespace(
        data_dir=tmp_path / "data",
        venv=tmp_path / "venv",
        port=None,
        web_only=False,
    )

    assert deploy_local.cmd_open(args) == 0
    assert calls == [args]


def test_open_starts_services_once_installed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.env_file.parent.mkdir(parents=True, exist_ok=True)
    settings.env_file.write_text("JLU_BOOKING_LOCAL_PORT=8123\n", encoding="utf-8")
    settings.web_cli.parent.mkdir(parents=True, exist_ok=True)
    settings.web_cli.write_text("#!/bin/sh\n", encoding="utf-8")

    calls = []
    monkeypatch.setattr(
        deploy_local,
        "start_web",
        lambda s, open_browser=False: calls.append(("web", open_browser)),
    )
    monkeypatch.setattr(
        deploy_local, "start_scheduler", lambda s: calls.append(("scheduler", True))
    )
    monkeypatch.setattr(
        deploy_local, "cmd_up", lambda args: pytest.fail("must not reinstall")
    )

    args = SimpleNamespace(
        data_dir=settings.data,
        venv=settings.venv,
        port=None,
        web_only=False,
    )

    assert deploy_local.cmd_open(args) == 0
    assert calls == [("web", True), ("scheduler", True)]

def test_quick_start_shell_script_is_valid():
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "quick-start.sh"
    assert script.is_file()
    if os.name != "nt":
        subprocess.run(["sh", "-n", str(script)], check=True)


def test_quick_start_scripts_expose_the_documented_entry_points():
    root = Path(__file__).resolve().parent.parent
    unix = (root / "scripts" / "quick-start.sh").read_text(encoding="utf-8")
    windows = (root / "scripts" / "quick-start.ps1").read_text(encoding="utf-8")

    assert "jlu-booking.git" in unix
    assert "exec ./jlu" in unix
    assert "JLU_BOOKING_HOME" in unix
    assert "tools\\deploy_local.py" in windows
    assert "JLU_BOOKING_HOME" in windows

def test_link_installs_a_global_jlu_command(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX symlink launcher")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("JLU_BOOKING_BIN_DIR", str(bin_dir))
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    )

    assert deploy_local.cmd_link(SimpleNamespace(quiet=False, auto_path=False)) == 0

    link = bin_dir / "jlu"
    assert link.is_symlink()
    assert link.resolve() == (deploy_local.ROOT / "jlu").resolve()

    # Running it again must not fail or duplicate anything.
    assert deploy_local.cmd_link(SimpleNamespace(quiet=True, auto_path=False)) == 0


def test_append_path_block_is_written_once(tmp_path):
    rc = tmp_path / "rc"
    directory = Path.home() / ".local" / "bin"

    assert deploy_local._append_path_block(directory, rc) is True
    assert deploy_local._append_path_block(directory, rc) is True

    text = rc.read_text(encoding="utf-8")
    assert text.count("JLU Booking launcher >>>") == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text
