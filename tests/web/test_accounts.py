from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.web.accounts import (
    AccountService,
    ActiveUserLimitReached,
    AuthenticationFailed,
    InvalidUsername,
    PendingUserLimitReached,
    UsernameUnavailable,
)
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import PasswordService, RateLimitExceeded, ThrottleService
from jlu_booking.web.sessions import SessionService


BEIJING = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def beijing_now():
    return datetime(2026, 9, 22, 6, 0, tzinfo=BEIJING)


@pytest.fixture
def database(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    return connection


@pytest.fixture
def sessions(database):
    return SessionService(database)


@pytest.fixture
def accounts(database, sessions):
    return AccountService(
        database,
        PasswordService(),
        sessions,
        ThrottleService(database),
    )


@pytest.fixture
def active_user(accounts, database, beijing_now):
    user = accounts.register_pending(
        "alice",
        "correct horse battery staple",
        source_ip="127.0.0.1",
        now=beijing_now,
    )
    database.execute(
        "UPDATE users SET status = 'active', activated_at = ?, "
        "pending_expires_at = NULL WHERE id = ?",
        (beijing_now.isoformat(), user.id),
    )
    return accounts.get(user.id)


def test_register_pending_normalizes_username_and_expires_in_24_hours(
    accounts, beijing_now
):
    user = accounts.register_pending(
        "Alice_01",
        "correct horse battery staple",
        source_ip="127.0.0.1",
        now=beijing_now,
    )

    assert user.username == "alice_01"
    assert user.status == "pending_token"
    assert user.pending_expires_at == beijing_now + timedelta(hours=24)


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 33, "name with spaces", "中文用户", "alice@example.com"],
)
def test_registration_rejects_invalid_usernames(accounts, beijing_now, username):
    with pytest.raises(InvalidUsername):
        accounts.register_pending(
            username,
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=beijing_now,
        )


def test_registration_rejects_case_insensitive_duplicate(accounts, beijing_now):
    accounts.register_pending(
        "Alice",
        "correct horse battery staple",
        source_ip="127.0.0.1",
        now=beijing_now,
    )

    with pytest.raises(UsernameUnavailable):
        accounts.register_pending(
            "ALICE",
            "another correct horse password",
            source_ip="127.0.0.2",
            now=beijing_now,
        )


def test_registration_limits_each_ip_to_five_attempts(accounts, beijing_now):
    for index in range(5):
        accounts.register_pending(
            f"user_{index}",
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=beijing_now,
        )

    with pytest.raises(RateLimitExceeded):
        accounts.register_pending(
            "user_6",
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=beijing_now,
        )


def test_registration_enforces_pending_cap(database, accounts, beijing_now):
    rows = [
        (
            f"pending_{index}",
            "hash",
            "user",
            "pending_token",
            beijing_now.isoformat(),
            (beijing_now + timedelta(hours=24)).isoformat(),
        )
        for index in range(100)
    ]
    database.executemany(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, pending_expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )

    with pytest.raises(PendingUserLimitReached):
        accounts.register_pending(
            "extra_user",
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=beijing_now,
        )


def test_cleanup_expired_pending_deletes_only_expired_records(
    database, accounts, beijing_now
):
    database.executemany(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, pending_expires_at) "
        "VALUES (?, 'hash', 'user', 'pending_token', ?, ?)",
        [
            (
                "expired_user",
                (beijing_now - timedelta(days=2)).isoformat(),
                beijing_now.isoformat(),
            ),
            (
                "valid_user",
                beijing_now.isoformat(),
                (beijing_now + timedelta(hours=1)).isoformat(),
            ),
        ],
    )

    assert accounts.cleanup_expired_pending(beijing_now) == 1
    assert accounts.find_by_username("expired_user") is None
    assert accounts.find_by_username("valid_user") is not None


def test_authentication_uses_generic_failure_for_unknown_wrong_and_disabled(
    accounts, database, active_user, beijing_now
):
    messages = []
    for username, password in (
        ("missing", "correct horse battery staple"),
        (active_user.username, "incorrect password"),
    ):
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate(
                username,
                password,
                source_ip="127.0.0.1",
                now=beijing_now,
            )
        messages.append(str(caught.value))

    database.execute(
        "UPDATE users SET status = 'disabled', disabled_at = ? WHERE id = ?",
        (beijing_now.isoformat(), active_user.id),
    )
    with pytest.raises(AuthenticationFailed) as caught:
        accounts.authenticate(
            active_user.username,
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=beijing_now,
        )
    messages.append(str(caught.value))

    assert len(set(messages)) == 1


def test_successful_login_clears_failure_throttle(
    accounts, database, active_user, beijing_now
):
    with pytest.raises(AuthenticationFailed):
        accounts.authenticate(
            active_user.username,
            "incorrect password",
            source_ip="127.0.0.1",
            now=beijing_now,
        )
    assert database.execute(
        "SELECT 1 FROM request_throttles WHERE bucket_key LIKE 'login:%'"
    ).fetchone()

    authenticated = accounts.authenticate(
        active_user.username,
        "correct horse battery staple",
        source_ip="127.0.0.1",
        now=beijing_now,
    )

    assert authenticated.id == active_user.id
    assert database.execute(
        "SELECT 1 FROM request_throttles WHERE bucket_key LIKE 'login:%'"
    ).fetchone() is None


def test_password_reset_forces_change_and_revokes_sessions(
    accounts, sessions, active_user, beijing_now
):
    grant = sessions.create(active_user.id, beijing_now)
    accounts.reset_password(
        active_user.id,
        "temporary password 42",
        now=beijing_now,
    )

    assert sessions.resolve(grant.raw_token, beijing_now) is None
    user = accounts.authenticate(
        active_user.username,
        "temporary password 42",
        source_ip="127.0.0.1",
        now=beijing_now,
    )
    assert user.must_change_password is True


def test_change_password_requires_current_password_and_revokes_sessions(
    accounts, sessions, active_user, beijing_now
):
    grant = sessions.create(active_user.id, beijing_now)
    with pytest.raises(AuthenticationFailed):
        accounts.change_password(
            active_user.id,
            "wrong password",
            "new correct horse password",
            now=beijing_now,
        )
    assert sessions.resolve(grant.raw_token, beijing_now) is not None

    accounts.change_password(
        active_user.id,
        "correct horse battery staple",
        "new correct horse password",
        now=beijing_now,
    )

    assert sessions.resolve(grant.raw_token, beijing_now) is None
    changed = accounts.authenticate(
        active_user.username,
        "new correct horse password",
        source_ip="127.0.0.1",
        now=beijing_now,
    )
    assert changed.must_change_password is False


def test_reenable_rechecks_active_user_limit(database, accounts, beijing_now):
    rows = [
        (
            f"active_{index}",
            "hash",
            "user",
            "active",
            beijing_now.isoformat(),
            beijing_now.isoformat(),
        )
        for index in range(30)
    ]
    database.executemany(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    database.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, disabled_at) "
        "VALUES ('disabled_user', 'hash', 'user', 'disabled', ?, ?)",
        (beijing_now.isoformat(), beijing_now.isoformat()),
    )
    disabled_id = database.execute(
        "SELECT id FROM users WHERE username = 'disabled_user'"
    ).fetchone()[0]

    with pytest.raises(ActiveUserLimitReached):
        accounts.enable(disabled_id, now=beijing_now)


def test_admin_creation_is_active_and_not_counted_as_booking_user(
    accounts, database, beijing_now
):
    admin = accounts.create_admin(
        "Owner",
        "correct horse battery staple",
        now=beijing_now,
    )

    assert admin.username == "owner"
    assert admin.role == "admin"
    assert admin.status == "active"
    count = database.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'user' AND status = 'active'"
    ).fetchone()[0]
    assert count == 0


def test_accounts_reject_naive_datetimes(accounts):
    with pytest.raises(ValueError, match="时区"):
        accounts.register_pending(
            "alice",
            "correct horse battery staple",
            source_ip="127.0.0.1",
            now=datetime(2026, 9, 22, 6, 0),
        )
