"""SQLite connections and versioned schema for the Web application."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
_TRANSACTION_LOCK = threading.RLock()

TASK_STATUSES = (
    "scheduled",
    "running",
    "success",
    "no_result",
    "token_invalid",
    "account_blocked",
    "daily_limit",
    "submission_unknown",
    "network_unavailable",
    "stopped",
    "error",
    "cancelled",
)
TERMINAL_TASK_STATUSES = tuple(
    status for status in TASK_STATUSES if status not in {"scheduled", "running"}
)


def _quoted_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


MIGRATION_1 = (
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
        status TEXT NOT NULL CHECK (
            status IN ('pending_token', 'active', 'disabled', 'deleted')
        ),
        must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (
            must_change_password IN (0, 1)
        ),
        created_at TEXT NOT NULL,
        activated_at TEXT,
        pending_expires_at TEXT,
        disabled_at TEXT,
        deleted_at TEXT
    )
    """,
    """
    CREATE TABLE user_credentials (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        token_ciphertext BLOB NOT NULL,
        token_blind_index BLOB NOT NULL UNIQUE,
        verified_at TEXT NOT NULL,
        last_status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE companions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        student_number_ciphertext BLOB NOT NULL,
        name_ciphertext BLOB NOT NULL,
        verified_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE booking_tasks (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        execution_date TEXT NOT NULL,
        target_day TEXT NOT NULL CHECK (target_day IN ('today', 'tomorrow')),
        venue TEXT NOT NULL,
        sport TEXT NOT NULL,
        companion_id INTEGER NOT NULL REFERENCES companions(id),
        preferred_court_number INTEGER NOT NULL CHECK (preferred_court_number > 0),
        time_priority_json TEXT NOT NULL,
        real_booking_enabled INTEGER NOT NULL DEFAULT 1 CHECK (
            real_booking_enabled IN (0, 1)
        ),
        status TEXT NOT NULL CHECK (status IN ({_quoted_values(TASK_STATUSES)})),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        claimed_at TEXT,
        stop_requested_at TEXT,
        cancelled_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX one_open_task_per_user
    ON booking_tasks(user_id)
    WHERE status IN ('scheduled', 'running')
    """,
    """
    CREATE INDEX tasks_by_execution_date
    ON booking_tasks(execution_date, status)
    """,
    f"""
    CREATE TABLE task_runs (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL UNIQUE
            REFERENCES booking_tasks(id) ON DELETE CASCADE,
        process_id INTEGER,
        runtime_path TEXT NOT NULL,
        log_path TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        exit_code INTEGER,
        final_status TEXT CHECK (
            final_status IS NULL
            OR final_status IN ({_quoted_values(TERMINAL_TASK_STATUSES)})
        ),
        detail TEXT
    )
    """,
    """
    CREATE TABLE web_sessions (
        id INTEGER PRIMARY KEY,
        token_hash BLOB NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_secret TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        reauthenticated_at TEXT
    )
    """,
    """
    CREATE TABLE request_throttles (
        bucket_key TEXT PRIMARY KEY,
        window_started_at TEXT NOT NULL,
        counter INTEGER NOT NULL CHECK (counter > 0)
    )
    """,
    """
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY,
        admin_id INTEGER NOT NULL REFERENCES users(id),
        action TEXT NOT NULL,
        target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        source_ip TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)


MIGRATION_2 = (
    """
    CREATE TABLE daily_booking_plans (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
        enabled_at TEXT,
        target_day TEXT NOT NULL CHECK (target_day IN ('today', 'tomorrow')),
        venue TEXT NOT NULL,
        sport TEXT NOT NULL,
        companion_id INTEGER NOT NULL REFERENCES companions(id),
        preferred_court_number INTEGER NOT NULL CHECK (preferred_court_number > 0),
        time_priority_json TEXT NOT NULL,
        real_booking_enabled INTEGER NOT NULL DEFAULT 0 CHECK (real_booking_enabled IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "ALTER TABLE booking_tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'one_shot' "
    "CHECK (source IN ('one_shot', 'daily'))",
    "ALTER TABLE booking_tasks ADD COLUMN daily_plan_id INTEGER REFERENCES daily_booking_plans(id)",
    """
    CREATE UNIQUE INDEX one_daily_task_per_plan_date
    ON booking_tasks(daily_plan_id, execution_date)
    WHERE source = 'daily' AND status != 'cancelled'
    """,
)


MIGRATION_3 = (
    """
    CREATE TABLE manual_candidates (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        venue TEXT NOT NULL,
        sport TEXT NOT NULL,
        query_date TEXT NOT NULL,
        court_name TEXT NOT NULL,
        place_short_name TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX manual_candidates_by_owner ON manual_candidates(user_id, expires_at)",
    """
    CREATE TABLE manual_booking_attempts (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        candidate_id TEXT NOT NULL REFERENCES manual_candidates(id),
        companion_id INTEGER NOT NULL REFERENCES companions(id),
        companion_updated_at TEXT NOT NULL,
        school_companion_id TEXT NOT NULL,
        credential_updated_at TEXT NOT NULL,
        confirmation_hash BLOB NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('prechecked', 'submitting', 'success', 'rejected', 'unknown')),
        kind TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE UNIQUE INDEX one_manual_submission_per_user
    ON manual_booking_attempts(user_id) WHERE status='submitting'
    """,
)


def connect_database(path: Path | str) -> sqlite3.Connection:
    """Open a configured SQLite connection for Web application state."""

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        database_path,
        timeout=5,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


@contextmanager
def transaction(
    connection: sqlite3.Connection,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Run a commit-or-rollback transaction on an autocommit connection."""

    # Web requests share one connection across worker threads. Serialize only
    # the short SQLite transaction, never a school HTTP request.
    with _TRANSACTION_LOCK:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def migrate_database(connection: sqlite3.Connection) -> None:
    """Apply all schema migrations once and safely on repeated startup."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    with transaction(connection, immediate=True):
        applied = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for version, statements in ((1, MIGRATION_1), (2, MIGRATION_2), (3, MIGRATION_3)):
            if version in applied:
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(BEIJING).isoformat()),
            )
