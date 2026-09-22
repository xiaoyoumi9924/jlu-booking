from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import (
    CredentialCipher,
    PasswordService,
    RateLimitExceeded,
    ThrottleService,
    mask_secret,
)
from jlu_booking.web.sessions import CsrfError, SessionService


BEIJING = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def beijing_now():
    return datetime(2026, 9, 22, 6, 0, tzinfo=BEIJING)


@pytest.fixture
def database(tmp_path, beijing_now):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    connection.execute(
        "INSERT INTO users "
        "(id, username, password_hash, role, status, created_at, activated_at) "
        "VALUES (1, 'alice', 'hash', 'user', 'active', ?, ?)",
        (beijing_now.isoformat(), beijing_now.isoformat()),
    )
    return connection


def test_password_service_hashes_and_verifies_without_storing_plaintext():
    passwords = PasswordService()

    password_hash = passwords.hash("correct horse battery staple")

    assert "correct horse battery staple" not in password_hash
    assert passwords.verify(password_hash, "correct horse battery staple") is True
    assert passwords.verify(password_hash, "wrong password") is False
    assert passwords.verify("not-an-argon2-hash", "wrong password") is False


def test_password_service_rejects_fewer_than_ten_characters():
    passwords = PasswordService()

    with pytest.raises(ValueError, match="至少 10"):
        passwords.hash("short")


def test_cipher_round_trip_and_blind_index_do_not_expose_token():
    cipher = CredentialCipher(Fernet.generate_key(), b"b" * 32)
    encrypted = cipher.encrypt("private-token")

    assert b"private-token" not in encrypted
    assert cipher.decrypt(encrypted) == "private-token"
    assert cipher.token_index("private-token") == cipher.token_index(
        " private-token "
    )
    assert cipher.token_index("private-token") != cipher.token_index(
        "other-token"
    )


def test_cipher_encrypts_companion_values_and_rejects_empty_text():
    cipher = CredentialCipher(Fernet.generate_key(), b"b" * 32)

    number = cipher.encrypt("20260001")
    name = cipher.encrypt("测试同学")

    assert b"20260001" not in number
    assert "测试同学".encode() not in name
    assert cipher.decrypt(number) == "20260001"
    assert cipher.decrypt(name) == "测试同学"
    with pytest.raises(ValueError, match="不能为空"):
        cipher.encrypt("   ")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a", "*"),
        ("ab", "**"),
        ("abcd", "a**d"),
        ("private-token", "priv*****oken"),
    ],
)
def test_mask_secret_never_returns_the_original(value, expected):
    assert mask_secret(value) == expected
    assert mask_secret(value) != value


def test_session_obeys_idle_and_absolute_expiry(database, beijing_now):
    service = SessionService(database)
    grant = service.create(user_id=1, now=beijing_now)

    assert service.resolve(grant.raw_token, beijing_now + timedelta(hours=11))
    assert service.resolve(grant.raw_token, beijing_now + timedelta(days=7)) is None


def test_session_expires_after_twelve_hours_of_inactivity(database, beijing_now):
    service = SessionService(database)
    grant = service.create(user_id=1, now=beijing_now)

    assert service.resolve(
        grant.raw_token,
        beijing_now + timedelta(hours=12),
    ) is None


def test_session_stores_only_token_hash_and_supports_revocation(
    database, beijing_now
):
    service = SessionService(database)
    first = service.create(user_id=1, now=beijing_now)
    second = service.create(user_id=1, now=beijing_now)

    stored = database.execute(
        "SELECT token_hash FROM web_sessions WHERE id = ?",
        (first.session_id,),
    ).fetchone()[0]
    assert first.raw_token.encode() not in stored

    service.invalidate_user_sessions(1, except_session_id=first.session_id)
    assert service.resolve(first.raw_token, beijing_now) is not None
    assert service.resolve(second.raw_token, beijing_now) is None


def test_csrf_validation_uses_constant_time_comparison(
    database, beijing_now, monkeypatch
):
    service = SessionService(database)
    grant = service.create(user_id=1, now=beijing_now)
    session = service.resolve(grant.raw_token, beijing_now)
    calls = []

    def compare(left, right):
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("jlu_booking.web.sessions.hmac.compare_digest", compare)

    service.require_csrf(session, grant.csrf_token)
    with pytest.raises(CsrfError):
        service.require_csrf(session, "wrong")
    assert calls == [
        (grant.csrf_token, grant.csrf_token),
        (grant.csrf_token, "wrong"),
    ]


def test_throttle_blocks_eleventh_login_failure(database, beijing_now):
    throttles = ThrottleService(database)
    key = "login:alice:127.0.0.1"
    for _ in range(10):
        throttles.consume(
            key,
            limit=10,
            window=timedelta(minutes=15),
            now=beijing_now,
        )

    with pytest.raises(RateLimitExceeded) as caught:
        throttles.consume(
            key,
            limit=10,
            window=timedelta(minutes=15),
            now=beijing_now,
        )
    assert 0 < caught.value.retry_after_seconds <= 900


def test_throttle_require_available_does_not_increment(database, beijing_now):
    throttles = ThrottleService(database)
    key = "token:1"

    for _ in range(3):
        throttles.require_available(
            key,
            limit=5,
            window=timedelta(minutes=15),
            now=beijing_now,
        )
    assert database.execute(
        "SELECT counter FROM request_throttles WHERE bucket_key = ?",
        (key,),
    ).fetchone() is None


@pytest.mark.parametrize("prefix", ["token", "reauth"])
def test_five_failure_limit_resets_after_window(
    database, beijing_now, prefix
):
    throttles = ThrottleService(database)
    key = f"{prefix}:1"
    for _ in range(5):
        throttles.consume(
            key,
            limit=5,
            window=timedelta(minutes=15),
            now=beijing_now,
        )

    with pytest.raises(RateLimitExceeded):
        throttles.require_available(
            key,
            limit=5,
            window=timedelta(minutes=15),
            now=beijing_now,
        )

    throttles.require_available(
        key,
        limit=5,
        window=timedelta(minutes=15),
        now=beijing_now + timedelta(minutes=15),
    )
    assert database.execute(
        "SELECT counter FROM request_throttles WHERE bucket_key = ?",
        (key,),
    ).fetchone() is None


def test_throttle_clear_removes_successful_attempt_bucket(database, beijing_now):
    throttles = ThrottleService(database)
    throttles.consume(
        "login:alice:127.0.0.1",
        limit=10,
        window=timedelta(minutes=15),
        now=beijing_now,
    )

    throttles.clear("login:alice:127.0.0.1")

    assert database.execute(
        "SELECT 1 FROM request_throttles"
    ).fetchone() is None


def test_services_reject_naive_datetimes(database):
    naive = datetime(2026, 9, 22, 6, 0)

    with pytest.raises(ValueError, match="时区"):
        SessionService(database).create(user_id=1, now=naive)
    with pytest.raises(ValueError, match="时区"):
        ThrottleService(database).consume(
            "login:alice:127.0.0.1",
            limit=10,
            window=timedelta(minutes=15),
            now=naive,
        )
