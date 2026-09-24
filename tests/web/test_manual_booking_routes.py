"""Browser confirmation and result pages use fake school calls only."""

from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.availability import AvailabilityResult
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.manual_booking import ManualBookingService
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import TaskService

NOW = datetime(2026, 9, 23, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def hidden(html, name):
    return re.search(rf'name="{name}" value="([^"]+)"', html).group(1)


@pytest.fixture
def web(tmp_path, monkeypatch):
    for module in ("jlu_booking.web.dependencies", "jlu_booking.web.routes.auth",
                   "jlu_booking.web.routes.dashboard", "jlu_booking.web.routes.manual_booking"):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: NOW, raising=False)
    path = tmp_path / "web.sqlite3"
    db = connect_database(path)
    migrate_database(db)
    passwords = PasswordService()
    throttle = ThrottleService(db)
    sessions = SessionService(db)
    accounts = AccountService(db, passwords, sessions, throttle)
    credentials = CredentialService(
        db, CredentialCipher(Fernet.generate_key(), b"b" * 32), throttle,
        token_validator=lambda _: TokenValidationResult("valid", "ok"),
        companion_validator=lambda _number, _token: {"id": 8, "name": "同行同学"},
    )
    users = {}
    for name in ("alice", "bob"):
        user = accounts.register_pending(name, "long password value", source_ip=name, now=NOW)
        credentials.activate_user(user.id, f"private-token-{name}", now=NOW)
        credentials.save_companion(user.id, f"student-{name}", now=NOW)
        users[name] = user.id
    books = []
    manual = ManualBookingService(
        path, credentials, can_book_func=lambda **_: {"msg": "success"},
        companion_func=lambda **_: {"id": 8},
        book_place_func=lambda **kwargs: books.append(kwargs) or {"msg": "success"},
    )
    services = AppServices(db, passwords, sessions, throttle, accounts, credentials, TaskService(db))
    services.manual_booking = manual
    settings = WebSettings(
        data_dir=tmp_path, database_path=path, runtime_root=tmp_path / "runtime",
        backup_dir=tmp_path / "backups", token_key_file=tmp_path / "token.key",
        blind_key_file=tmp_path / "blind.key", token_key=Fernet.generate_key(),
        blind_key=b"b" * 32, cookie_secure=False,
    )
    with TestClient(create_app(settings, services)) as client:
        yield client, manual, db, accounts, users, books
    db.close()


def login(client, name):
    page = client.get("/login")
    response = client.post("/login", data={
        "username": name, "password": "long password value", "csrf_token": csrf(page.text),
    })
    assert response.status_code == 200


def prepare(web):
    client, manual, _db, _accounts, users, _books = web
    candidate = manual.register_candidates(users["alice"], AvailabilityResult(
        "前卫体育馆", "羽毛球", "2026-09-23", NOW,
        ({"court_name": "1号场", "place_short_name": "ymq1", "start": "15:30", "end": "17:30"},),
    ))[0]
    login(client, "alice")
    checked = client.post("/manual/precheck", data={
        "candidate_id": candidate["candidate_id"], "csrf_token": csrf(client.get("/").text),
    })
    assert checked.status_code == 200
    return checked, {"attempt_id": hidden(checked.text, "attempt_id"),
                     "nonce": hidden(checked.text, "nonce"),
                     "csrf_token": csrf(checked.text)}


def test_confirm_result_refresh_and_replay_do_not_resubmit(web):
    client, _manual, _db, _accounts, _users, books = web
    checked, fields = prepare(web)
    assert "private-token-alice" not in checked.text
    denied = client.post("/manual/submit", data={**fields, "csrf_token": "invalid"})
    assert denied.status_code == 403 and books == []
    first = client.post("/manual/submit", data=fields, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["cache-control"] == "no-store"
    assert books and len(books) == 1
    result = client.get(first.headers["location"])
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert "预约成功" in result.text
    assert "private-token-alice" not in result.text
    assert "student-alice" not in result.text
    assert 'action="/manual/submit"' not in result.text
    assert client.get(first.headers["location"]).status_code == 200
    replay = client.post("/manual/submit", data=fields, follow_redirects=False)
    assert replay.status_code == 303 and len(books) == 1
    client.cookies.clear()
    login(client, "bob")
    assert client.get(first.headers["location"]).status_code == 404
    assert len(books) == 1


def test_app_startup_reconciles_orphaned_submission_without_network(web):
    client, _manual, db, _accounts, _users, books = web
    _checked, fields = prepare(web)
    db.execute("UPDATE manual_booking_attempts SET status='submitting' WHERE id=?", (fields["attempt_id"],))
    # A new Web process marks the abandoned attempt unknown before any route is served.
    create_app(client.app.state.settings, client.app.state.services)
    page = client.get(f"/manual/result/{fields['attempt_id']}")
    assert "结果不明" in page.text and "不要重复提交" in page.text
    assert books == []
