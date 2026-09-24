"""Booking history is read-only and scoped to the signed-in Web account."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.profile_history import list_history
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.tasks import TaskDraft, TaskService


NOW = datetime(2026, 9, 24, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def history_db(tmp_path):
    connection = connect_database(tmp_path / "history.sqlite3")
    migrate_database(connection)
    passwords = PasswordService()
    accounts = AccountService(connection, passwords, SessionService(connection), ThrottleService(connection))
    key = Fernet.generate_key()
    credentials = CredentialService(
        connection, CredentialCipher(key, b"b" * 32), ThrottleService(connection),
        token_validator=lambda _: TokenValidationResult("valid", "ok"),
        companion_validator=lambda _number, _token: {"id": 1, "name": "同学"},
    )
    ids = {}
    for name in ("alice", "bob"):
        user = accounts.register_pending(name, "long password value", source_ip=name, now=NOW)
        credentials.activate_user(user.id, f"private-token-{name}", now=NOW)
        credentials.save_companion(user.id, f"2026{name}", now=NOW)
        ids[name] = (user.id, credentials.decrypt_companion(user.id).id)
    yield connection, ids
    connection.close()


def _manual(connection, user_id, companion_id, candidate_id, attempt_id, status, updated_at):
    connection.execute(
        "INSERT INTO manual_candidates (id,user_id,venue,sport,query_date,court_name,"
        "place_short_name,start_time,end_time,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (candidate_id, user_id, "前卫体育馆", "羽毛球", "2026-09-25", "1号场", "ymq1",
         "15:30", "17:30", updated_at, updated_at),
    )
    connection.execute(
        "INSERT INTO manual_booking_attempts "
        "(id,user_id,candidate_id,companion_id,companion_updated_at,school_companion_id,"
        "credential_updated_at,confirmation_hash,status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, user_id, candidate_id, companion_id, updated_at, "1", updated_at,
         attempt_id.encode(), status, updated_at, updated_at),
    )


def test_history_combines_only_owners_submitted_records(history_db):
    connection, ids = history_db
    alice_id, companion_id = ids["alice"]
    task = TaskService(connection).create(alice_id, TaskDraft(
        "tomorrow", "前卫体育馆", "羽毛球", companion_id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    _manual(connection, alice_id, companion_id, "candidate-1", "attempt-1", "success", "2026-09-24T08:00:00+08:00")
    _manual(connection, alice_id, companion_id, "candidate-2", "attempt-2", "prechecked", "2026-09-24T09:00:00+08:00")
    bob_id, bob_companion = ids["bob"]
    _manual(connection, bob_id, bob_companion, "candidate-3", "attempt-3", "rejected", "2026-09-24T10:00:00+08:00")

    alice = list_history(connection, alice_id)
    assert {(item.kind, item.record_id) for item in alice.items} == {
        ("auto", str(task.id)), ("manual", "attempt-1")
    }
    assert next(item.target_date for item in alice.items if item.kind == "auto") == "2026-09-25"
    assert all(item.status != "prechecked" for item in alice.items)
    assert {item.record_id for item in list_history(connection, bob_id).items} == {"attempt-3"}


def test_history_paginates_in_stable_order(history_db):
    connection, ids = history_db
    user_id, companion_id = ids["alice"]
    for index in range(25):
        task = TaskService(connection).create(user_id, TaskDraft(
            "today", "前卫体育馆", "羽毛球", companion_id, 3,
            [["15:30", "17:30"]], False,
        ), now=NOW)
        connection.execute("UPDATE booking_tasks SET status='cancelled',created_at=? WHERE id=?",
                           (f"2026-09-24T06:{index:02d}:00+08:00", task.id))
    first = list_history(connection, user_id, page=1)
    second = list_history(connection, user_id, page=2)
    assert len(first.items) == 20 and first.has_next
    assert len(second.items) == 5 and not second.has_next
    assert {item.record_id for item in first.items}.isdisjoint(item.record_id for item in second.items)
    assert first.items[0].occurred_at >= first.items[-1].occurred_at


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), (1, 51)])
def test_history_rejects_invalid_pagination(history_db, page, page_size):
    connection, ids = history_db
    with pytest.raises(ValueError):
        list_history(connection, ids["alice"][0], page=page, page_size=page_size)
