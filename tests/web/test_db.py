import base64
import os
import sqlite3

import pytest

from jlu_booking.web.db import connect_database, migrate_database, transaction
from jlu_booking.web.settings import WebSettings


def _write_keys(tmp_path, *, token_mode=0o600, blind_mode=0o600):
    token_key = tmp_path / "token.key"
    blind_key = tmp_path / "blind.key"
    token_key.write_bytes(base64.urlsafe_b64encode(b"t" * 32))
    blind_key.write_bytes(base64.urlsafe_b64encode(b"b" * 32))
    token_key.chmod(token_mode)
    blind_key.chmod(blind_mode)
    return token_key, blind_key


def test_database_migration_is_idempotent_and_enables_safety_pragmas(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    migrate_database(connection)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "schema_migrations",
        "users",
        "user_credentials",
        "companions",
        "booking_tasks",
        "task_runs",
        "web_sessions",
        "request_throttles",
        "audit_events",
    } <= tables
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 1"
    ).fetchone()[0] == 1


def test_each_database_connection_enables_foreign_keys(tmp_path):
    path = tmp_path / "web.sqlite3"
    first = connect_database(path)
    migrate_database(first)
    second = connect_database(path)

    assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_creates_one_open_task_partial_unique_index(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)

    index = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'index' AND name = 'one_open_task_per_user'"
    ).fetchone()

    assert index is not None
    normalized = " ".join(index["sql"].lower().split())
    assert "unique index" in normalized
    assert "where status in ('scheduled', 'running')" in normalized


def test_transaction_rolls_back_and_immediate_mode_releases_lock(tmp_path):
    path = tmp_path / "web.sqlite3"
    connection = connect_database(path)
    migrate_database(connection)

    with pytest.raises(RuntimeError, match="stop"):
        with transaction(connection, immediate=True):
            connection.execute(
                "INSERT INTO users "
                "(username, password_hash, role, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("alice", "hash", "user", "pending_token", "2026-09-22T00:00:00+08:00"),
            )
            raise RuntimeError("stop")

    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    second = connect_database(path)
    second.execute("BEGIN IMMEDIATE")
    second.rollback()


def test_web_settings_resolves_paths_and_limits(tmp_path):
    token_key, blind_key = _write_keys(tmp_path)

    settings = WebSettings.from_env(
        {
            "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
            "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(token_key),
            "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(blind_key),
            "JLU_BOOKING_WEB_COOKIE_SECURE": "false",
            "JLU_BOOKING_WEB_TRUSTED_PROXY": "127.0.0.1",
        }
    )

    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.database_path == settings.data_dir / "web.sqlite3"
    assert settings.runtime_root == settings.data_dir / "runtime"
    assert settings.backup_dir == settings.data_dir / "backups"
    assert settings.cookie_secure is False
    assert settings.trusted_proxy == "127.0.0.1"
    assert settings.user_limit == 30
    assert settings.pending_limit == 100
    assert settings.daily_task_limit == 10
    assert settings.token_key == base64.urlsafe_b64encode(b"t" * 32)
    assert settings.blind_key == b"b" * 32


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_web_settings_rejects_group_readable_secret_files(tmp_path):
    token_key, blind_key = _write_keys(tmp_path, token_mode=0o644)

    with pytest.raises(ValueError, match="权限"):
        WebSettings.from_env(
            {
                "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
                "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(token_key),
                "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(blind_key),
            }
        )


def test_web_settings_rejects_missing_or_invalid_keys(tmp_path):
    token_key, blind_key = _write_keys(tmp_path)
    missing = tmp_path / "missing.key"

    with pytest.raises(ValueError, match="不存在"):
        WebSettings.from_env(
            {
                "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
                "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(missing),
                "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(blind_key),
            }
        )

    token_key.write_text("not-a-fernet-key", encoding="ascii")
    with pytest.raises(ValueError, match="Fernet"):
        WebSettings.from_env(
            {
                "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
                "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(token_key),
                "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(blind_key),
            },
            strict_permissions=False,
        )


def test_booking_task_status_constraint_rejects_unknown_value(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("alice", "hash", "user", "active", "2026-09-22T00:00:00+08:00"),
    )
    connection.execute(
        "INSERT INTO companions "
        "(user_id, student_number_ciphertext, name_ciphertext, verified_at, updated_at) "
        "VALUES (1, ?, ?, ?, ?)",
        (
            sqlite3.Binary(b"student"),
            sqlite3.Binary(b"name"),
            "2026-09-22T00:00:00+08:00",
            "2026-09-22T00:00:00+08:00",
        ),
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO booking_tasks "
            "(user_id, execution_date, target_day, venue, sport, companion_id, "
            "preferred_court_number, time_priority_json, status, created_at, updated_at) "
            "VALUES (1, '2026-09-23', 'today', '前卫体育馆', '羽毛球', 1, "
            "3, '[]', 'unknown', ?, ?)",
            ("2026-09-22T00:00:00+08:00", "2026-09-22T00:00:00+08:00"),
        )
