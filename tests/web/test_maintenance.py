import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.maintenance import BackupService, MaintenanceService


BEIJING = ZoneInfo("Asia/Shanghai")


def test_maintenance_removes_expired_records_and_old_logs_without_following_symlinks(
    tmp_path,
):
    now = datetime(2026, 9, 22, 3, 15, tzinfo=BEIJING)
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    pending_id = connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, pending_expires_at) "
        "VALUES ('pending', 'hash', 'user', 'pending_token', ?, ?)",
        ((now - timedelta(days=2)).isoformat(), (now - timedelta(days=1)).isoformat()),
    ).lastrowid
    active_id = connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at) "
        "VALUES ('active', 'hash', 'user', 'active', ?, ?)",
        (now.isoformat(), now.isoformat()),
    ).lastrowid
    connection.execute(
        "INSERT INTO web_sessions "
        "(token_hash, user_id, csrf_secret, created_at, last_seen_at, expires_at) "
        "VALUES (?, ?, 'csrf', ?, ?, ?)",
        (b"expired", active_id, now.isoformat(), (now - timedelta(days=1)).isoformat(), (now + timedelta(days=1)).isoformat()),
    )
    connection.execute(
        "INSERT INTO request_throttles (bucket_key, window_started_at, counter) "
        "VALUES ('old', ?, 1)", ((now - timedelta(days=2)).isoformat(),)
    )
    runtime = tmp_path / "runtime"
    logs = runtime / "user-1" / "task-1" / "logs"
    logs.mkdir(parents=True)
    old_log = logs / "worker.log"
    old_log.write_text("old", encoding="utf-8")
    current_log = logs / "current.log"
    current_log.write_text("current", encoding="utf-8")
    old_timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old_log, (old_timestamp, old_timestamp))
    outside = tmp_path / "outside.log"
    outside.write_text("keep", encoding="utf-8")
    os.utime(outside, (old_timestamp, old_timestamp))
    (runtime / "linked").symlink_to(tmp_path, target_is_directory=True)

    result = MaintenanceService(connection, runtime).run(now)

    assert result.expired_sessions == 1
    assert result.expired_throttles == 1
    assert result.expired_pending_users == 1
    assert result.deleted_logs == 1
    assert connection.execute("SELECT 1 FROM users WHERE id = ?", (pending_id,)).fetchone() is None
    assert not old_log.exists()
    assert current_log.exists()
    assert outside.exists()


def test_backup_is_atomic_private_and_retains_latest_fourteen(tmp_path):
    database_path = tmp_path / "data" / "web.sqlite3"
    connection = connect_database(database_path)
    migrate_database(connection)
    connection.execute(
        "INSERT INTO users (username, password_hash, role, status, created_at) "
        "VALUES ('owner', 'hash', 'admin', 'active', '2026-09-22T00:00:00+08:00')"
    )
    backup_dir = tmp_path / "backups"
    service = BackupService(connection, backup_dir)
    for offset in range(16):
        service.create(datetime(2026, 9, 1 + offset, 3, 15, tzinfo=BEIJING))

    backups = sorted(backup_dir.glob("*.sqlite3"))
    assert len(backups) == 14
    assert backups[0].name == "2026-09-03.sqlite3"
    assert backups[-1].name == "2026-09-16.sqlite3"
    if os.name != "nt":
        assert backups[-1].stat().st_mode & 0o777 == 0o600
    restored = connect_database(backups[-1])
    assert restored.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert not list(backup_dir.glob("*.tmp"))


def test_maintenance_refuses_symlink_runtime_root(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "runtime"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        MaintenanceService(connection, linked).run(
            datetime(2026, 9, 22, 3, 15, tzinfo=BEIJING)
        )


def test_maintenance_removes_old_terminal_runtime_directory(tmp_path):
    now = datetime(2026, 9, 22, 3, 15, tzinfo=BEIJING)
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    user_id = connection.execute(
        "INSERT INTO users (username,password_hash,role,status,created_at) "
        "VALUES ('alice','hash','user','active',?)", (now.isoformat(),)
    ).lastrowid
    companion_id = connection.execute(
        "INSERT INTO companions (user_id,student_number_ciphertext,name_ciphertext,verified_at,updated_at) "
        "VALUES (?,X'01',X'02',?,?)", (user_id, now.isoformat(), now.isoformat())
    ).lastrowid
    task_id = connection.execute(
        "INSERT INTO booking_tasks (user_id,execution_date,target_day,venue,sport,companion_id,preferred_court_number,time_priority_json,status,created_at,updated_at) "
        "VALUES (?,'2026-08-01','today','前卫体育馆','羽毛球',?,3,'[]','success',?,?)",
        (user_id, companion_id, now.isoformat(), now.isoformat()),
    ).lastrowid
    runtime = tmp_path / "runtime" / f"user-{user_id}" / f"task-{task_id}"
    runtime.mkdir(parents=True)
    (runtime / "auto_booking.json").write_text("private", encoding="utf-8")
    old = (now - timedelta(days=31)).timestamp()
    os.utime(runtime, (old, old))
    connection.execute(
        "INSERT INTO task_runs (task_id,runtime_path,log_path,started_at,finished_at,final_status) "
        "VALUES (?,?,?,?,?,'success')",
        (task_id, str(runtime), str(runtime / 'worker.log'), now.isoformat(), now.isoformat()),
    )
    result = MaintenanceService(connection, tmp_path / "runtime").run(now)
    assert result.deleted_runtime_directories == 1
    assert not runtime.exists()
