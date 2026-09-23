"""Offline manual booking candidate ownership and safety tests."""

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.api import ServerResponseError
from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.availability import AvailabilityResult
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.availability import AvailabilityService
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import MIGRATION_1, MIGRATION_2, connect_database, migrate_database
from jlu_booking.web.manual_booking import CandidateUnavailable, ManualBookingError, ManualBookingService
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import TaskService

BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 23, 6, 30, tzinfo=BEIJING)


@pytest.fixture
def state(tmp_path):
    db_path = tmp_path / "web.sqlite3"
    connection = connect_database(db_path)
    migrate_database(connection)
    passwords = PasswordService()
    throttles = ThrottleService(connection)
    sessions = SessionService(connection)
    accounts = AccountService(connection, passwords, sessions, throttles)
    credentials = CredentialService(
        connection, CredentialCipher(Fernet.generate_key(), b"b" * 32), throttles,
        token_validator=lambda _: TokenValidationResult("valid", "ok"),
        companion_validator=lambda _number, _token: {"id": 8, "name": "同行同学"},
    )
    users = {}
    for username in ("alice", "bob"):
        user = accounts.register_pending(username, "long password value",
                                         source_ip=username, now=NOW)
        credentials.activate_user(user.id, f"private-token-{username}", now=NOW)
        credentials.save_companion(user.id, f"student-{username}", now=NOW)
        users[username] = user.id
    yield db_path, connection, credentials, accounts, users
    connection.close()


def result(*, court="1号场"):
    return AvailabilityResult(
        "前卫体育馆", "羽毛球", "2026-09-23", NOW,
        ({"court_name": court, "court_id": 1, "place_short_name": "ymq1",
          "start": "15:30", "end": "17:30"},),
    )


def make_service(state, *, can_book_func=None, companion_func=None, book_place_func=None):
    path, _db, credentials, _accounts, _users = state
    return ManualBookingService(
        path, credentials,
        can_book_func=can_book_func or (lambda **_: {"msg": "success"}),
        companion_func=companion_func or (lambda **_: {"id": 8, "name": "同行同学"}),
        book_place_func=book_place_func or (lambda **_: {"msg": "success"}),
    )


def test_migration_three_is_idempotent(state):
    _path, db, _credentials, _accounts, _users = state
    migrate_database(db)
    assert {row[0] for row in db.execute("SELECT version FROM schema_migrations")} == {1, 2, 3}
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"manual_candidates", "manual_booking_attempts"} <= tables


def test_version_two_database_upgrades_once(tmp_path):
    db = connect_database(tmp_path / "historical.sqlite3")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, statements in ((1, MIGRATION_1), (2, MIGRATION_2)):
        for statement in statements:
            db.execute(statement)
        db.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, NOW.isoformat()))
    migrate_database(db)
    migrate_database(db)
    assert [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3]


def test_owned_candidate_precheck_uses_server_snapshot_and_expires(state):
    _path, db, _credentials, accounts, users = state
    can_calls = []
    companion_calls = []
    book_calls = []
    service = make_service(
        state,
        can_book_func=lambda **kwargs: can_calls.append(kwargs) or {"msg": "success"},
        companion_func=lambda **kwargs: companion_calls.append(kwargs) or {"id": 8},
        book_place_func=lambda **kwargs: book_calls.append(kwargs),
    )
    candidate = service.register_candidates(users["alice"], result())[0]
    candidate_id = candidate["candidate_id"]
    with pytest.raises(CandidateUnavailable):
        service.precheck(users["bob"], candidate_id, NOW)
    with pytest.raises(CandidateUnavailable):
        service.precheck(users["alice"], candidate_id, NOW + timedelta(minutes=2))
    assert not can_calls
    precheck = service.precheck(users["alice"], candidate_id, NOW + timedelta(seconds=1))
    assert precheck.court_name == "1号场"
    assert precheck.nonce and precheck.attempt_id
    assert can_calls[0]["query_date"] == "2026-09-23"
    assert can_calls[0]["place_short_name"] == "ymq1"
    assert can_calls[0]["start_time"] == "15:30"
    assert can_calls[0]["token"] == "private-token-alice"
    assert companion_calls[0]["student_number"] == "student-alice"
    assert book_calls == []
    row = db.execute("SELECT * FROM manual_candidates WHERE id=?", (candidate_id,)).fetchone()
    assert "token" not in " ".join(row.keys()).lower()
    assert "student" not in " ".join(row.keys()).lower()
    assert "private-token-alice" not in repr(tuple(row))
    accounts.disable(users["alice"], now=NOW)
    with pytest.raises(CandidateUnavailable):
        service.precheck(users["alice"], candidate_id, NOW + timedelta(seconds=2))


def test_precheck_rejection_has_no_nonce_or_submission(state):
    _path, db, _credentials, _accounts, users = state
    book_calls = []
    service = make_service(
        state, can_book_func=lambda **_: (_ for _ in ()).throw(ServerResponseError({"msg": "场地已被预约"})),
        book_place_func=lambda **kwargs: book_calls.append(kwargs),
    )
    candidate_id = service.register_candidates(users["alice"], result())[0]["candidate_id"]
    with pytest.raises(ManualBookingError) as raised:
        service.precheck(users["alice"], candidate_id, NOW)
    assert raised.value.kind == "target_unavailable"
    assert db.execute("SELECT COUNT(*) FROM manual_booking_attempts").fetchone()[0] == 0
    assert book_calls == []


def test_invalid_slot_shape_is_not_registered(state):
    _path, db, _credentials, _accounts, users = state
    service = make_service(state)
    assert service.register_candidates(users["alice"], result(court="")) == []
    assert db.execute("SELECT COUNT(*) FROM manual_candidates").fetchone()[0] == 0


def test_query_to_precheck_route_never_submits_and_hides_token(state, tmp_path, monkeypatch):
    path, db, credentials, accounts, users = state
    for module in ("jlu_booking.web.dependencies", "jlu_booking.web.routes.auth",
                   "jlu_booking.web.routes.availability", "jlu_booking.web.routes.dashboard",
                   "jlu_booking.web.routes.manual_booking"):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: NOW, raising=False)
    key = Fernet.generate_key()
    settings = WebSettings(
        data_dir=tmp_path, database_path=path, runtime_root=tmp_path / "runtime",
        backup_dir=tmp_path / "backups", token_key_file=tmp_path / "token.key",
        blind_key_file=tmp_path / "blind.key", token_key=key, blind_key=b"b" * 32,
        cookie_secure=False,
    )
    services = AppServices(
        db, accounts._passwords, accounts._sessions, accounts._throttles,
        accounts, credentials, TaskService(db),
    )
    services.availability = AvailabilityService(
        credentials, accounts._throttles,
        query_func=lambda **_: {"placeArray": [{"projectName": {
            "name": "1号场", "id": 1, "shortname": "ymq1",
        }, "projectInfo": [{"state": 1, "starttime": "15:30", "endtime": "17:30"}]}]},
    )
    books = []
    services.manual_booking = make_service(
        state, can_book_func=lambda **_: {"msg": "success"},
        book_place_func=lambda **kwargs: books.append(kwargs),
    )
    def csrf(html):
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    def login(client, username):
        page = client.get("/login")
        client.post("/login", data={"username": username, "password": "long password value",
                                    "csrf_token": csrf(page.text)})
    with TestClient(create_app(settings, services)) as client:
        login(client, "alice")
        query = client.post("/availability/query", data={
            "csrf_token": csrf(client.get("/").text), "venue": "前卫体育馆",
            "sport": "羽毛球", "target_day": "today",
        })
        assert query.status_code == 200
        candidate_id = re.search(r'name="candidate_id" value="([^"]+)"', query.text).group(1)
        checked = client.post("/manual/precheck", data={
            "csrf_token": csrf(query.text), "candidate_id": candidate_id,
            "venue": "伪造场馆", "place_short_name": "fake",
        })
        assert checked.status_code == 200
        assert "1号场" in checked.text and "确认真实预约" in checked.text
        assert "private-token-alice" not in checked.text
        assert "student-alice" not in checked.text
        assert books == []
        client.cookies.clear()
        login(client, "bob")
        denied = client.post("/manual/precheck", data={
            "csrf_token": csrf(client.get("/").text), "candidate_id": candidate_id,
        })
        assert denied.status_code == 400
        assert books == []
