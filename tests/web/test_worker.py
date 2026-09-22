import io
import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.config import DEFAULT_TIME_PRIORITY
from jlu_booking.run_status import write_run_status
from jlu_booking.web.tasks import BookingTask
from jlu_booking.web.worker import WorkerAdapter


BEIJING = ZoneInfo("Asia/Shanghai")


class FakeCredentials:
    def __init__(self, token="private-token", student_number="20260001"):
        self.token = token
        self.companion = SimpleNamespace(
            id=7,
            user_id=11,
            student_number=student_number,
            name="测试同学",
        )

    def decrypt_token(self, user_id):
        assert user_id == self.companion.user_id
        return self.token

    def decrypt_companion(self, user_id):
        assert user_id == self.companion.user_id
        return self.companion


@pytest.fixture
def task():
    now = datetime(2026, 9, 22, 6, 30, tzinfo=BEIJING)
    return BookingTask(
        id=19,
        user_id=11,
        execution_date=now.date(),
        target_day="today",
        venue="前卫体育馆",
        sport="羽毛球",
        companion_id=7,
        preferred_court_number=3,
        time_priority=tuple(DEFAULT_TIME_PRIORITY),
        real_booking_enabled=False,
        status="running",
        created_at=now,
        updated_at=now,
        claimed_at=now,
        stop_requested_at=None,
        cancelled_at=None,
    )


@pytest.fixture
def worker_adapter(tmp_path):
    settings = SimpleNamespace(runtime_root=tmp_path / "workers")
    return WorkerAdapter(
        FakeCredentials(),
        settings,
        python_executable="python-web",
        base_environment={"SAFE_PARENT": "yes"},
    )


def test_worker_launch_isolates_paths_and_keeps_token_out_of_files(
    worker_adapter, task
):
    launch = worker_adapter.prepare(task)
    config_text = launch.config_path.read_text(encoding="utf-8")

    assert launch.environment["JLU_BOOKING_TOKEN"] == "private-token"
    assert launch.environment["JLU_BOOKING_CONFIG_FILE"] == str(launch.config_path)
    assert launch.environment["JLU_BOOKING_RUNTIME_DIR"] == str(launch.runtime_dir)
    assert launch.environment["SAFE_PARENT"] == "yes"
    assert "private-token" not in config_text
    assert "private-token" not in " ".join(launch.command)
    if os.name != "nt":
        assert oct(launch.config_path.stat().st_mode & 0o777) == "0o600"
        assert oct(launch.runtime_dir.stat().st_mode & 0o777) == "0o700"


def test_two_users_receive_disjoint_paths(tmp_path, task):
    settings = SimpleNamespace(runtime_root=tmp_path / "workers")
    first = WorkerAdapter(FakeCredentials(), settings).prepare(task)
    second_credentials = FakeCredentials(token="other-token")
    second_credentials.companion = SimpleNamespace(
        id=8,
        user_id=12,
        student_number="20260001",
        name="测试同学",
    )
    second_task = replace(task, id=20, user_id=12, companion_id=8)
    second = WorkerAdapter(second_credentials, settings).prepare(second_task)
    assert first.runtime_dir != second.runtime_dir
    assert first.config_path != second.config_path
    assert first.runtime_dir == tmp_path / "workers" / "user-11" / "task-19"
    assert second.runtime_dir == tmp_path / "workers" / "user-12" / "task-20"


@pytest.mark.parametrize(
    ("target_day", "expected"), [("today", "今天"), ("tomorrow", "明天")]
)
def test_snapshot_maps_target_day_and_preserves_priority(
    worker_adapter, task, target_day, expected
):
    launch = worker_adapter.prepare(replace(task, target_day=target_day))
    snapshot = json.loads(launch.config_path.read_text(encoding="utf-8"))
    assert snapshot["target_day"] == expected
    assert snapshot["time_priority"] == [list(item) for item in DEFAULT_TIME_PRIORITY]
    assert snapshot["companion_student_number"] == "20260001"


def test_command_mode_matches_real_booking_setting(worker_adapter, task):
    scan = worker_adapter.prepare(task)
    real = worker_adapter.prepare(replace(task, id=20, real_booking_enabled=True))
    assert scan.command == ("python-web", "-m", "jlu_booking.auto", "--dry-run")
    assert real.command == ("python-web", "-m", "jlu_booking.auto")


def test_log_sanitizer_redacts_exact_private_values(worker_adapter, task):
    worker_adapter.prepare(task)
    line = "token=private-token companion=20260001"
    assert worker_adapter.sanitize_line(task, line) == (
        "token=[REDACTED] companion=[REDACTED]"
    )


def test_start_uses_direct_process_and_pumps_sanitized_log(
    monkeypatch, worker_adapter, task
):
    captured = {}

    class FakeProcess:
        pid = 1234
        stdout = io.StringIO("token=private-token companion=20260001\n")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            captured["terminated"] = True

        def kill(self):
            captured["killed"] = True

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("jlu_booking.web.worker.subprocess.Popen", fake_popen)
    launch = worker_adapter.prepare(task)
    running = worker_adapter.start(launch)
    running.join_log_pump()

    assert captured["command"] == list(launch.command)
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is subprocess.STDOUT
    assert "private-token" not in launch.log_path.read_text(encoding="utf-8")
    assert "20260001" not in launch.log_path.read_text(encoding="utf-8")
    assert running.poll() == 0


@pytest.mark.parametrize(
    "status",
    [
        "success",
        "no_result",
        "token_invalid",
        "account_blocked",
        "daily_limit",
        "submission_unknown",
        "network_unavailable",
        "stopped",
    ],
)
def test_collect_result_preserves_terminal_status_and_deletes_snapshot(
    worker_adapter, task, status
):
    launch = worker_adapter.prepare(task)
    write_run_status(
        status,
        path=launch.runtime_dir / "state" / "last_run.json",
        detail="safe-detail",
    )
    result = worker_adapter.collect_result(task, launch, exit_code=0)
    assert result.status == status
    assert result.detail == "safe-detail"
    assert not launch.config_path.exists()


@pytest.mark.parametrize("payload", [None, "not-json", '{"status":"running"}'])
def test_collect_result_maps_missing_corrupt_or_nonterminal_state_to_error(
    worker_adapter, task, payload
):
    launch = worker_adapter.prepare(task)
    status_path = launch.runtime_dir / "state" / "last_run.json"
    if payload is not None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(payload, encoding="utf-8")
    result = worker_adapter.collect_result(task, launch, exit_code=1)
    assert result.status == "error"
    assert "private-token" not in result.detail
    assert not launch.config_path.exists()


def test_prepare_rejects_symlinked_user_directory(tmp_path, task):
    root = tmp_path / "workers"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "user-11").symlink_to(elsewhere, target_is_directory=True)
    adapter = WorkerAdapter(FakeCredentials(), SimpleNamespace(runtime_root=root))
    with pytest.raises(ValueError, match="符号链接"):
        adapter.prepare(task)


def test_recovery_cleanup_removes_private_snapshot_but_keeps_state(
    worker_adapter, task
):
    launch = worker_adapter.prepare(task)
    state = launch.runtime_dir / "state" / "last_run.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"status":"success"}', encoding="utf-8")
    worker_adapter.cleanup_recovered_snapshot(launch.runtime_dir)
    assert not launch.config_path.exists()
    assert state.exists()
