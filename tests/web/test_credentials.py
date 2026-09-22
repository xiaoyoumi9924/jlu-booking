import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import ActiveUserLimitReached
from jlu_booking.web.credentials import (
    CompanionValidationFailed,
    CredentialService,
    DuplicateToken,
    ReauthenticationRequired,
    TokenValidationFailed,
)
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import CredentialCipher, RateLimitExceeded, ThrottleService


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
def cipher():
    return CredentialCipher(Fernet.generate_key(), b"b" * 32)


def _insert_user(connection, username, now, *, status="pending_token"):
    activated_at = now.isoformat() if status == "active" else None
    pending_expires_at = (
        (now + timedelta(hours=24)).isoformat()
        if status == "pending_token"
        else None
    )
    cursor = connection.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at, "
        "pending_expires_at) VALUES (?, 'hash', 'user', ?, ?, ?, ?)",
        (
            username,
            status,
            now.isoformat(),
            activated_at,
            pending_expires_at,
        ),
    )
    return cursor.lastrowid


def _valid_token(_token):
    return TokenValidationResult(status="valid", reason="accepted")


def _valid_companion(student_number, token):
    assert student_number == "20260001"
    assert token == "private-token"
    return {"id": 42, "name": "测试同学"}


def _service(
    connection,
    cipher,
    *,
    token_validator=_valid_token,
    companion_validator=_valid_companion,
    reauth_checker=None,
):
    return CredentialService(
        connection,
        cipher,
        ThrottleService(connection),
        token_validator=token_validator,
        companion_validator=companion_validator,
        reauth_checker=reauth_checker,
    )


def test_successful_activation_encrypts_token_and_activates_user(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    service = _service(database, cipher)

    summary = service.activate_user(user_id, " private-token ", now=beijing_now)

    row = database.execute(
        "SELECT token_ciphertext, token_blind_index, last_status "
        "FROM user_credentials WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    assert b"private-token" not in row["token_ciphertext"]
    assert b"private-token" not in row["token_blind_index"]
    assert row["last_status"] == "valid"
    assert service.decrypt_token(user_id) == "private-token"
    assert summary.masked_token == "priv*****oken"
    assert database.execute(
        "SELECT status FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0] == "active"


@pytest.mark.parametrize("status", ["invalid", "unavailable", "account_blocked"])
def test_failed_activation_does_not_store_token_or_leak_it(
    database, cipher, beijing_now, status
):
    user_id = _insert_user(database, f"user_{status}", beijing_now)
    service = _service(
        database,
        cipher,
        token_validator=lambda _token: TokenValidationResult(status, "safe"),
    )

    with pytest.raises(TokenValidationFailed) as caught:
        service.activate_user(user_id, "private-token", now=beijing_now)

    assert caught.value.status == status
    assert "private-token" not in str(caught.value)
    assert database.execute(
        "SELECT 1 FROM user_credentials WHERE user_id = ?", (user_id,)
    ).fetchone() is None
    assert database.execute(
        "SELECT status FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0] == "pending_token"


def test_duplicate_token_cannot_activate_two_users(database, cipher, beijing_now):
    alice_id = _insert_user(database, "alice", beijing_now)
    bob_id = _insert_user(database, "bob", beijing_now)
    service = _service(database, cipher)
    service.activate_user(alice_id, "private-token", now=beijing_now)

    with pytest.raises(DuplicateToken):
        service.activate_user(bob_id, "private-token", now=beijing_now)

    assert database.execute(
        "SELECT status FROM users WHERE id = ?", (bob_id,)
    ).fetchone()[0] == "pending_token"
    assert database.execute(
        "SELECT 1 FROM user_credentials WHERE user_id = ?", (bob_id,)
    ).fetchone() is None


def test_failed_replacement_keeps_old_ciphertext(database, cipher, beijing_now):
    user_id = _insert_user(database, "alice", beijing_now)
    valid_service = _service(database, cipher)
    valid_service.activate_user(user_id, "private-token", now=beijing_now)
    before = tuple(
        database.execute(
            "SELECT token_ciphertext, token_blind_index, verified_at, last_status, "
            "updated_at FROM user_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    )
    failed_service = _service(
        database,
        cipher,
        token_validator=lambda _token: TokenValidationResult(
            "unavailable", "transport"
        ),
    )

    with pytest.raises(TokenValidationFailed):
        failed_service.replace_token(user_id, "new-private-token", now=beijing_now)

    after = tuple(
        database.execute(
            "SELECT token_ciphertext, token_blind_index, verified_at, last_status, "
            "updated_at FROM user_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    )
    assert after == before
    assert valid_service.decrypt_token(user_id) == "private-token"


def test_successful_replacement_updates_token_and_status(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    service = _service(database, cipher)
    service.activate_user(user_id, "private-token", now=beijing_now)

    summary = service.replace_token(
        user_id,
        "replacement-token",
        now=beijing_now + timedelta(minutes=1),
    )

    assert service.decrypt_token(user_id) == "replacement-token"
    assert summary.masked_token == "repl*********oken"
    assert summary.last_status == "valid"


def test_companion_is_validated_encrypted_and_returned_masked(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    service = _service(database, cipher)
    service.activate_user(user_id, "private-token", now=beijing_now)

    summary = service.save_companion(user_id, " 20260001 ", now=beijing_now)

    row = database.execute(
        "SELECT student_number_ciphertext, name_ciphertext FROM companions "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    assert b"20260001" not in row["student_number_ciphertext"]
    assert "测试同学".encode() not in row["name_ciphertext"]
    assert summary.masked_student_number == "2******1"
    assert summary.name == "测试同学"
    decrypted = service.decrypt_companion(user_id)
    assert decrypted.student_number == "20260001"
    assert decrypted.name == "测试同学"


def test_invalid_companion_does_not_store_private_input(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    service = _service(
        database,
        cipher,
        companion_validator=lambda _number, _token: (_ for _ in ()).throw(
            RuntimeError("server rejected")
        ),
    )
    service.activate_user(user_id, "private-token", now=beijing_now)

    with pytest.raises(CompanionValidationFailed) as caught:
        service.save_companion(user_id, "20260001", now=beijing_now)

    assert "20260001" not in str(caught.value)
    assert database.execute("SELECT 1 FROM companions").fetchone() is None


def test_sixth_failed_token_validation_is_blocked_before_gateway(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    calls = []

    def invalid(token):
        calls.append(token)
        return TokenValidationResult("invalid", "auth_rejected")

    service = _service(database, cipher, token_validator=invalid)
    for _ in range(5):
        with pytest.raises(TokenValidationFailed):
            service.activate_user(user_id, "private-token", now=beijing_now)

    with pytest.raises(RateLimitExceeded):
        service.activate_user(user_id, "private-token", now=beijing_now)
    assert calls == ["private-token"] * 5


def test_reveal_requires_reauthentication_and_audits_without_token(
    database, cipher, beijing_now
):
    user_id = _insert_user(database, "alice", beijing_now)
    database.execute(
        "INSERT INTO users "
        "(username, password_hash, role, status, created_at, activated_at) "
        "VALUES ('owner', 'hash', 'admin', 'active', ?, ?)",
        (beijing_now.isoformat(), beijing_now.isoformat()),
    )
    admin_id = database.execute(
        "SELECT id FROM users WHERE username = 'owner'"
    ).fetchone()[0]
    service = _service(
        database,
        cipher,
        reauth_checker=lambda _admin_id, _now: False,
    )
    service.activate_user(user_id, "private-token", now=beijing_now)

    with pytest.raises(ReauthenticationRequired):
        service.reveal_token(
            admin_id,
            user_id,
            source_ip="127.0.0.1",
            now=beijing_now,
        )

    allowed = _service(
        database,
        cipher,
        reauth_checker=lambda checked_id, _now: checked_id == admin_id,
    )
    assert allowed.reveal_token(
        admin_id,
        user_id,
        source_ip="127.0.0.1",
        now=beijing_now,
    ) == "private-token"
    event = database.execute(
        "SELECT action, target_user_id, metadata_json FROM audit_events"
    ).fetchone()
    assert event["action"] == "token_revealed"
    assert event["target_user_id"] == user_id
    assert "private-token" not in event["metadata_json"]
    assert json.loads(event["metadata_json"]) == {}


def test_default_gateways_use_beijing_date_and_existing_companion_api(
    database, cipher, beijing_now, monkeypatch
):
    user_id = _insert_user(database, "alice", beijing_now)
    token_calls = []
    companion_calls = []

    def validate(token, **kwargs):
        token_calls.append((token, kwargs))
        return TokenValidationResult("valid", "accepted")

    def companion(**kwargs):
        companion_calls.append(kwargs)
        return {"id": 42, "name": "测试同学"}

    monkeypatch.setattr("jlu_booking.web.credentials.validate_token_online", validate)
    monkeypatch.setattr("jlu_booking.web.credentials.get_companion_user", companion)
    service = CredentialService(
        database,
        cipher,
        ThrottleService(database),
        clock=lambda: beijing_now,
    )

    service.activate_user(user_id, "private-token", now=beijing_now)
    service.save_companion(user_id, "20260001", now=beijing_now)

    assert token_calls[0][0] == "private-token"
    assert token_calls[0][1]["query_date"] == "2026-09-22"
    assert companion_calls == [
        {"student_number": "20260001", "token": "private-token"}
    ]


def test_concurrent_final_slot_activation_allows_exactly_one_user(
    tmp_path, cipher, beijing_now
):
    path = tmp_path / "web.sqlite3"
    seed = connect_database(path)
    migrate_database(seed)
    for index in range(29):
        _insert_user(seed, f"active_{index}", beijing_now, status="active")
    alice_id = _insert_user(seed, "alice", beijing_now)
    bob_id = _insert_user(seed, "bob", beijing_now)
    seed.close()
    barrier = threading.Barrier(2)
    outcomes = []

    def activate(user_id, token):
        connection = connect_database(path)

        def validator(_token):
            barrier.wait(timeout=5)
            return TokenValidationResult("valid", "accepted")

        service = _service(connection, cipher, token_validator=validator)
        try:
            service.activate_user(user_id, token, now=beijing_now)
        except Exception as exc:
            outcomes.append(exc)
        else:
            outcomes.append("active")
        finally:
            connection.close()

    first = threading.Thread(target=activate, args=(alice_id, "alice-token"))
    second = threading.Thread(target=activate, args=(bob_id, "bob-token"))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert outcomes.count("active") == 1
    assert sum(isinstance(item, ActiveUserLimitReached) for item in outcomes) == 1
    check = connect_database(path)
    assert check.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'user' AND status = 'active'"
    ).fetchone()[0] == 30
    assert check.execute("SELECT COUNT(*) FROM user_credentials").fetchone()[0] == 1
