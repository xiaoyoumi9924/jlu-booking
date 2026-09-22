import json
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.tasks import (
    CredentialUnavailable,
    ExecutionDateFull,
    OpenTaskExists,
    TaskDraft,
    TaskFrozen,
    TaskNotFound,
    TaskNotRunning,
    TaskService,
    UserUnavailable,
)


BEIJING = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def database(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    return connection


@pytest.fixture
def task_service(database):
    return TaskService(database)


@pytest.fixture
def clock():
    return datetime(2026, 9, 22, 6, 30, tzinfo=BEIJING)


def _add_user(connection, username, now, *, status="active", credential="valid"):
    cursor = connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at) "
        "VALUES (?, 'hash', 'user', ?, ?, ?)",
        (username, status, now.isoformat(), now.isoformat()),
    )
    user_id = cursor.lastrowid
    if credential is not None:
        connection.execute(
            "INSERT INTO user_credentials "
            "(user_id, token_ciphertext, token_blind_index, verified_at, "
            "last_status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                f"cipher-{username}".encode(),
                f"blind-{username}".encode(),
                now.isoformat(),
                credential,
                now.isoformat(),
            ),
        )
    companion = connection.execute(
        "INSERT INTO companions "
        "(user_id, student_number_ciphertext, name_ciphertext, verified_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, b"student", b"name", now.isoformat(), now.isoformat()),
    ).lastrowid
    return user_id, companion


def _draft(companion_id, **overrides):
    values = {
        "target_day": "today",
        "venue": "前卫体育馆",
        "sport": "羽毛球",
        "companion_id": companion_id,
        "preferred_court_number": 3,
        "time_priority": [["15:30", "17:30"], ["17:30", "19:30"]],
        "real_booking_enabled": False,
    }
    values.update(overrides)
    return TaskDraft(**values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-22T07:26:59.999999+08:00", date(2026, 9, 22)),
        ("2026-09-22T07:27:00+08:00", date(2026, 9, 23)),
        ("2026-09-21T23:27:00+00:00", date(2026, 9, 23)),
    ],
)
def test_next_execution_date_uses_beijing_0727_boundary(
    task_service, value, expected
):
    assert task_service.next_execution_date(datetime.fromisoformat(value)) == expected


def test_next_execution_date_rejects_naive_time(task_service):
    with pytest.raises(ValueError, match="时区"):
        task_service.next_execution_date(datetime(2026, 9, 22, 6, 0))


def test_target_date_is_fixed_relative_to_execution_date(task_service):
    assert task_service.target_date(date(2026, 9, 23), "today") == date(2026, 9, 23)
    assert task_service.target_date(date(2026, 9, 23), "tomorrow") == date(2026, 9, 24)
    with pytest.raises(ValueError, match="target_day"):
        task_service.target_date(date(2026, 9, 23), "下周")


def test_create_stores_normalized_one_shot_task(database, task_service, clock):
    user_id, companion_id = _add_user(database, "alice", clock)

    task = task_service.create(user_id, _draft(companion_id), now=clock)

    assert task.user_id == user_id
    assert task.execution_date == date(2026, 9, 22)
    assert task.status == "scheduled"
    assert task.real_booking_enabled is False
    assert task.time_priority[0] == ("15:30", "17:30")
    stored = database.execute(
        "SELECT time_priority_json FROM booking_tasks WHERE id = ?", (task.id,)
    ).fetchone()[0]
    assert json.loads(stored)[0] == ["15:30", "17:30"]


@pytest.mark.parametrize(
    "changes",
    [
        {"target_day": "later"},
        {"venue": "不存在的场馆"},
        {"sport": "网球"},
        {"preferred_court_number": 0},
        {"time_priority": []},
    ],
)
def test_create_rejects_invalid_draft(database, task_service, clock, changes):
    user_id, companion_id = _add_user(database, "alice", clock)
    with pytest.raises(ValueError):
        task_service.create(user_id, _draft(companion_id, **changes), now=clock)
    assert database.execute("SELECT COUNT(*) FROM booking_tasks").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("status", "credential"),
    [("disabled", "valid"), ("pending_token", "valid"), ("active", None)],
)
def test_create_requires_active_user_and_valid_credential(
    database, task_service, clock, status, credential
):
    user_id, companion_id = _add_user(
        database, "alice", clock, status=status, credential=credential
    )
    expected = CredentialUnavailable if status == "active" else UserUnavailable
    with pytest.raises(expected):
        task_service.create(user_id, _draft(companion_id), now=clock)


def test_create_rejects_non_valid_credential_status(database, task_service, clock):
    user_id, companion_id = _add_user(
        database, "alice", clock, credential="invalid"
    )
    with pytest.raises(CredentialUnavailable):
        task_service.create(user_id, _draft(companion_id), now=clock)


def test_create_rejects_missing_or_foreign_companion(database, task_service, clock):
    alice_id, _ = _add_user(database, "alice", clock)
    _, bob_companion = _add_user(database, "bob", clock)
    with pytest.raises(ValueError, match="同行人"):
        task_service.create(alice_id, _draft(99999), now=clock)
    with pytest.raises(ValueError, match="同行人"):
        task_service.create(alice_id, _draft(bob_companion), now=clock)


def test_second_open_task_for_user_is_rejected(database, task_service, clock):
    user_id, companion_id = _add_user(database, "alice", clock)
    task_service.create(user_id, _draft(companion_id), now=clock)
    with pytest.raises(OpenTaskExists):
        task_service.create(user_id, _draft(companion_id), now=clock)


def test_update_before_cutoff_preserves_execution_date(database, task_service, clock):
    user_id, companion_id = _add_user(database, "alice", clock)
    task = task_service.create(user_id, _draft(companion_id), now=clock)
    updated = task_service.update(
        user_id,
        task.id,
        _draft(
            companion_id,
            target_day="tomorrow",
            preferred_court_number=5,
            real_booking_enabled=True,
        ),
        now=clock + timedelta(minutes=1),
    )
    assert updated.execution_date == task.execution_date
    assert updated.target_day == "tomorrow"
    assert updated.preferred_court_number == 5
    assert updated.real_booking_enabled is True


def test_update_and_cancel_freeze_at_exact_cutoff(database, task_service, clock):
    user_id, companion_id = _add_user(database, "alice", clock)
    task = task_service.create(user_id, _draft(companion_id), now=clock)
    cutoff = datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING)
    with pytest.raises(TaskFrozen):
        task_service.update(user_id, task.id, _draft(companion_id), now=cutoff)
    with pytest.raises(TaskFrozen):
        task_service.cancel(user_id, task.id, now=cutoff)


def test_cancel_releases_capacity_and_is_owner_scoped(database, task_service, clock):
    alice_id, companion_id = _add_user(database, "alice", clock)
    bob_id, _ = _add_user(database, "bob", clock)
    task = task_service.create(alice_id, _draft(companion_id), now=clock)
    with pytest.raises(TaskNotFound):
        task_service.get_for_user(bob_id, task.id)
    with pytest.raises(TaskNotFound):
        task_service.cancel(bob_id, task.id, now=clock)
    cancelled = task_service.cancel(alice_id, task.id, now=clock)
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == clock


def test_request_stop_requires_running_and_is_idempotent(
    database, task_service, clock
):
    user_id, companion_id = _add_user(database, "alice", clock)
    task = task_service.create(user_id, _draft(companion_id), now=clock)
    with pytest.raises(TaskNotRunning):
        task_service.request_stop(user_id, task.id, now=clock)
    database.execute(
        "UPDATE booking_tasks SET status = 'running' WHERE id = ?", (task.id,)
    )
    stopped = task_service.request_stop(user_id, task.id, now=clock)
    assert stopped.stop_requested_at == clock
    again = task_service.request_stop(
        user_id, task.id, now=clock + timedelta(minutes=1)
    )
    assert again.stop_requested_at == clock


def test_request_stop_rolls_back_when_audit_callback_fails(
    database, task_service, clock
):
    user_id, companion_id = _add_user(database, "alice", clock)
    task = task_service.create(user_id, _draft(companion_id), now=clock)
    database.execute(
        "UPDATE booking_tasks SET status = 'running' WHERE id = ?", (task.id,)
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        task_service.request_stop(
            user_id,
            task.id,
            now=clock,
            on_success=lambda: (_ for _ in ()).throw(
                RuntimeError("audit unavailable")
            ),
        )

    assert task_service.get_for_user(user_id, task.id).stop_requested_at is None


def test_tenth_execution_slot_is_transactional(tmp_path, clock):
    path = tmp_path / "capacity.sqlite3"
    setup = connect_database(path)
    migrate_database(setup)
    for index in range(9):
        user_id, companion_id = _add_user(setup, f"existing{index}", clock)
        TaskService(setup).create(user_id, _draft(companion_id), now=clock)
    contenders = [
        _add_user(setup, "contender_a", clock),
        _add_user(setup, "contender_b", clock),
    ]
    setup.close()
    barrier = threading.Barrier(2)
    outcomes = []

    def submit(user_id, companion_id):
        connection = connect_database(path)
        service = TaskService(connection)
        barrier.wait()
        try:
            outcomes.append(service.create(user_id, _draft(companion_id), now=clock))
        except Exception as exc:  # captured for assertions in the main thread
            outcomes.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=submit, args=ids) for ids in contenders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check = connect_database(path)
    assert sum(hasattr(value, "execution_date") for value in outcomes) == 1
    assert sum(isinstance(value, ExecutionDateFull) for value in outcomes) == 1
    assert check.execute(
        "SELECT COUNT(*) FROM booking_tasks "
        "WHERE execution_date = '2026-09-22' AND status != 'cancelled'"
    ).fetchone()[0] == 10
