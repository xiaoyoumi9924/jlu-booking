from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.run_status import write_run_status
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.daily_plans import DailyPlanService
from jlu_booking.web.scheduler import Scheduler
from jlu_booking.web.tasks import TaskDraft, TaskService
from jlu_booking.web.worker import WorkerResult


BEIJING = ZoneInfo("Asia/Shanghai")


def _insert_task(connection, *, username, execution_date="2026-09-22", status="scheduled"):
    now = "2026-09-22T06:00:00+08:00"
    user_id = connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at) "
        "VALUES (?, 'hash', 'user', 'active', ?, ?)",
        (username, now, now),
    ).lastrowid
    connection.execute(
        "INSERT INTO user_credentials "
        "(user_id, token_ciphertext, token_blind_index, verified_at, last_status, updated_at) "
        "VALUES (?, ?, ?, ?, 'valid', ?)",
        (user_id, f"cipher-{username}".encode(), f"blind-{username}".encode(), now, now),
    )
    companion_id = connection.execute(
        "INSERT INTO companions "
        "(user_id, student_number_ciphertext, name_ciphertext, verified_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, b"student", b"name", now, now),
    ).lastrowid
    task_id = connection.execute(
        "INSERT INTO booking_tasks "
        "(user_id, execution_date, target_day, venue, sport, companion_id, "
        "preferred_court_number, time_priority_json, real_booking_enabled, "
        "status, created_at, updated_at) "
        "VALUES (?, ?, 'today', '前卫体育馆', '羽毛球', ?, 3, "
        "'[[\"15:30\",\"17:30\"]]', 0, ?, ?, ?)",
        (user_id, execution_date, companion_id, status, now, now),
    ).lastrowid
    return user_id, task_id


class FakeRunning:
    def __init__(self):
        self.exit_code = None
        self.terminated = False
        self.pid = 8001

    def poll(self):
        return self.exit_code

    def terminate(self, grace_seconds=5.0):
        assert grace_seconds == 5.0
        self.terminated = True
        self.exit_code = -15
        return self.exit_code


class FakeWorker:
    def __init__(self, runtime_root):
        self.runtime_root = Path(runtime_root)
        self.started_task_ids = []
        self.running = {}
        self.cleaned = []
        self.results = {}

    def prepare(self, task):
        runtime = self.runtime_root / f"user-{task.user_id}" / f"task-{task.id}"
        runtime.mkdir(parents=True, exist_ok=True)
        config = runtime / "auto_booking.json"
        config.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            task_id=task.id,
            runtime_dir=runtime,
            log_path=runtime / "worker.log",
            config_path=config,
        )

    def start(self, launch):
        self.started_task_ids.append(launch.task_id)
        running = FakeRunning()
        self.running[launch.task_id] = running
        return running

    def collect_result(self, task, launch, exit_code):
        self.cleanup_snapshot(launch)
        return self.results.get(
            task.id, WorkerResult("success", "completed", exit_code)
        )

    def cleanup_snapshot(self, launch):
        launch.config_path.unlink(missing_ok=True)
        self.cleaned.append(launch.task_id)


@pytest.fixture
def database(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    return connection


@pytest.mark.parametrize(
    ("clock", "expected_starts"),
    [
        ("2026-09-22T07:26:59.999999+08:00", 0),
        ("2026-09-22T07:27:00+08:00", 1),
        ("2026-09-22T07:29:56.999999+08:00", 1),
        ("2026-09-22T07:29:57+08:00", 0),
        ("2026-09-22T07:33:00+08:00", 0),
    ],
)
def test_scheduler_only_claims_in_start_window(
    tmp_path, clock, expected_starts
):
    connection = connect_database(tmp_path / f"{clock[-14:-12]}.sqlite3")
    migrate_database(connection)
    _insert_task(connection, username="alice")
    worker = FakeWorker(tmp_path / "runtime")
    scheduler = Scheduler(connection, worker)

    tick = scheduler.run_once(datetime.fromisoformat(clock))

    assert len(worker.started_task_ids) == expected_starts
    assert len(tick.started_task_ids) == expected_starts
    if clock >= "2026-09-22T07:29:57":
        assert connection.execute(
            "SELECT status FROM booking_tasks"
        ).fetchone()[0] == "error"


def test_one_tick_claims_every_due_task_and_writes_run_rows(database, tmp_path):
    task_ids = [_insert_task(database, username=f"u{index}")[1] for index in range(3)]
    worker = FakeWorker(tmp_path / "runtime")
    scheduler = Scheduler(database, worker)

    tick = scheduler.run_once(datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING))

    assert tick.started_task_ids == tuple(task_ids)
    assert worker.started_task_ids == task_ids
    assert database.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 3
    assert database.execute(
        "SELECT COUNT(*) FROM booking_tasks WHERE status = 'running'"
    ).fetchone()[0] == 3


def test_scheduler_materializes_before_cutoff_and_claims_without_delay(database, tmp_path):
    user_id, old_id = _insert_task(database, username="daily", execution_date="2026-09-21", status="no_result")
    companion_id = database.execute("SELECT id FROM companions WHERE user_id=?", (user_id,)).fetchone()[0]
    plans = DailyPlanService(database)
    draft = TaskDraft("today", "前卫体育馆", "羽毛球", companion_id, 3,
                      [["15:30", "17:30"]], False)
    now = datetime(2026, 9, 22, 6, 0, tzinfo=BEIJING)
    plans.save(user_id, draft, now=now)
    plans.set_enabled(user_id, True, now=now)
    worker = FakeWorker(tmp_path / "runtime")
    scheduler = Scheduler(database, worker, daily_plans=plans)
    assert scheduler.run_once(now).started_task_ids == ()
    scheduled = database.execute(
        "SELECT id FROM booking_tasks WHERE source='daily' AND status='scheduled'"
    ).fetchone()[0]
    tick = scheduler.run_once(datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING))
    assert tick.started_task_ids == (scheduled,)
    assert worker.started_task_ids == [scheduled]
    assert old_id != scheduled


def test_stop_request_terminates_only_owned_worker(database, tmp_path):
    user_id, task_id = _insert_task(database, username="alice")
    worker = FakeWorker(tmp_path / "runtime")
    scheduler = Scheduler(database, worker)
    scheduler.run_once(datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING))
    database.execute(
        "UPDATE booking_tasks SET stop_requested_at = ? WHERE id = ?",
        ("2026-09-22T07:28:00+08:00", task_id),
    )

    tick = scheduler.run_once(datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING))

    assert tick.stopped_task_ids == (task_id,)
    assert worker.running[task_id].terminated is True
    task = TaskService(database).get_for_user(user_id, task_id)
    assert task.status == "stopped"


def test_finished_worker_preserves_submission_unknown_and_updates_run(
    database, tmp_path
):
    user_id, task_id = _insert_task(database, username="alice")
    worker = FakeWorker(tmp_path / "runtime")
    worker.results[task_id] = WorkerResult(
        "submission_unknown", "manual check", 3
    )
    scheduler = Scheduler(database, worker)
    scheduler.run_once(datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING))
    worker.running[task_id].exit_code = 3

    tick = scheduler.run_once(datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING))

    assert tick.finished_task_ids == (task_id,)
    assert TaskService(database).get_for_user(user_id, task_id).status == (
        "submission_unknown"
    )
    row = database.execute("SELECT final_status, exit_code FROM task_runs").fetchone()
    assert tuple(row) == ("submission_unknown", 3)


@pytest.mark.parametrize(
    ("status_text", "expected"),
    [("success", "success"), ("submission_unknown", "submission_unknown"), (None, "error")],
)
def test_reconcile_running_task_never_restarts(
    database, tmp_path, status_text, expected
):
    user_id, task_id = _insert_task(database, username="alice", status="running")
    runtime = tmp_path / "runtime" / f"task-{task_id}"
    runtime.mkdir(parents=True)
    database.execute(
        "INSERT INTO task_runs "
        "(task_id, process_id, runtime_path, log_path, started_at) "
        "VALUES (?, 123, ?, ?, ?)",
        (task_id, str(runtime), str(runtime / "worker.log"), "2026-09-22T07:27:00+08:00"),
    )
    if status_text is not None:
        write_run_status(
            status_text,
            path=runtime / "state" / "last_run.json",
            detail="safe",
        )
    worker = FakeWorker(tmp_path / "new-runtime")
    scheduler = Scheduler(database, worker)

    result = scheduler.reconcile(datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING))

    assert result.reconciled_task_ids == (task_id,)
    assert worker.started_task_ids == []
    assert TaskService(database).get_for_user(user_id, task_id).status == expected


def test_reconcile_corrupt_status_becomes_error(database, tmp_path):
    user_id, task_id = _insert_task(database, username="alice", status="running")
    runtime = tmp_path / "runtime" / f"task-{task_id}"
    (runtime / "state").mkdir(parents=True)
    (runtime / "state" / "last_run.json").write_text("not-json", encoding="utf-8")
    database.execute(
        "INSERT INTO task_runs (task_id, runtime_path, log_path, started_at) "
        "VALUES (?, ?, ?, ?)",
        (task_id, str(runtime), str(runtime / "worker.log"), "2026-09-22T07:27:00+08:00"),
    )
    scheduler = Scheduler(database, FakeWorker(tmp_path / "other"))
    scheduler.reconcile(datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING))
    assert TaskService(database).get_for_user(user_id, task_id).status == "error"


def test_recovery_at_0728_starts_once_but_core_boundary_marks_missed(tmp_path):
    early = connect_database(tmp_path / "early.sqlite3")
    migrate_database(early)
    _insert_task(early, username="early")
    early_worker = FakeWorker(tmp_path / "early-runtime")
    Scheduler(early, early_worker).run_once(
        datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING)
    )
    assert len(early_worker.started_task_ids) == 1

    late = connect_database(tmp_path / "late.sqlite3")
    migrate_database(late)
    _, late_id = _insert_task(late, username="late")
    late_worker = FakeWorker(tmp_path / "late-runtime")
    tick = Scheduler(late, late_worker).run_once(
        datetime(2026, 9, 22, 7, 29, 57, tzinfo=BEIJING)
    )
    assert late_worker.started_task_ids == []
    assert tick.missed_task_ids == (late_id,)
    run = late.execute("SELECT final_status, detail FROM task_runs").fetchone()
    assert run["final_status"] == "error"
    assert "07:29:57" in run["detail"]


@pytest.mark.parametrize("final_status", ["token_invalid", "account_blocked"])
def test_credential_status_follows_worker_security_result(
    database, tmp_path, final_status
):
    user_id, task_id = _insert_task(database, username="alice")
    worker = FakeWorker(tmp_path / "runtime")
    worker.results[task_id] = WorkerResult(final_status, "safe", 1)
    scheduler = Scheduler(database, worker)
    scheduler.run_once(datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING))
    worker.running[task_id].exit_code = 1
    scheduler.run_once(datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING))
    assert database.execute(
        "SELECT last_status FROM user_credentials WHERE user_id = ?", (user_id,)
    ).fetchone()[0] == final_status


@pytest.mark.parametrize(
    ("user_status", "credential_status"),
    [("disabled", "valid"), ("active", "token_invalid")],
)
def test_scheduler_refuses_ineligible_task_owner(
    database, tmp_path, user_status, credential_status
):
    user_id, task_id = _insert_task(database, username="alice")
    database.execute("UPDATE users SET status=? WHERE id=?", (user_status, user_id))
    database.execute(
        "UPDATE user_credentials SET last_status=? WHERE user_id=?",
        (credential_status, user_id),
    )
    worker = FakeWorker(tmp_path / "runtime")
    Scheduler(database, worker).run_once(
        datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING)
    )
    assert worker.started_task_ids == []
    assert database.execute(
        "SELECT status FROM booking_tasks WHERE id=?", (task_id,)
    ).fetchone()[0] == "error"


def test_prior_day_scheduled_task_is_finalized_after_restart(database, tmp_path):
    _, task_id = _insert_task(
        database, username="alice", execution_date="2026-09-21"
    )
    worker = FakeWorker(tmp_path / "runtime")
    tick = Scheduler(database, worker).run_once(
        datetime(2026, 9, 22, 6, 0, tzinfo=BEIJING)
    )
    assert tick.missed_task_ids == (task_id,)
    assert worker.started_task_ids == []
    assert database.execute(
        "SELECT status FROM booking_tasks WHERE id=?", (task_id,)
    ).fetchone()[0] == "error"
