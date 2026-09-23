"""Owned, read-only school availability queries."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import TaskService


BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 23, 6, 30, tzinfo=BEIJING)


def _csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _login(client, username):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": "long password value", "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


@pytest.fixture
def web(tmp_path, monkeypatch):
    from jlu_booking.web.availability import AvailabilityService

    for module in (
        "jlu_booking.web.dependencies",
        "jlu_booking.web.routes.auth",
        "jlu_booking.web.routes.dashboard",
        "jlu_booking.web.routes.availability",
    ):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: NOW, raising=False)
    token_key = Fernet.generate_key()
    settings = WebSettings(
        data_dir=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        runtime_root=tmp_path / "runtime",
        backup_dir=tmp_path / "backups",
        token_key_file=tmp_path / "token.key",
        blind_key_file=tmp_path / "blind.key",
        token_key=token_key,
        blind_key=b"b" * 32,
        cookie_secure=False,
    )
    connection = connect_database(settings.database_path)
    migrate_database(connection)
    passwords = PasswordService()
    sessions = SessionService(connection)
    throttles = ThrottleService(connection)
    accounts = AccountService(connection, passwords, sessions, throttles)
    credentials = CredentialService(
        connection,
        CredentialCipher(token_key, b"b" * 32),
        throttles,
        token_validator=lambda _token: TokenValidationResult("valid", "ok"),
    )
    services = AppServices(
        connection, passwords, sessions, throttles, accounts, credentials,
        TaskService(connection),
    )
    services.availability = AvailabilityService(
        credentials,
        throttles,
        query_func=lambda **_kwargs: {"placeArray": []},
    )
    for username in ("alice", "bob"):
        user = accounts.register_pending(
            username, "long password value", source_ip=username, now=NOW
        )
        credentials.activate_user(user.id, f"token-{username}", now=NOW)
    accounts.create_admin("owner", "long password value", now=NOW)
    app = create_app(settings, services)
    with TestClient(app) as client:
        yield client, services
    connection.close()


def test_query_endpoint_requires_owned_session_and_csrf(web):
    client, services = web
    anonymous = client.post("/availability/query", data={
        "venue": "前卫体育馆", "sport": "羽毛球", "target_day": "today",
    }, follow_redirects=False)
    assert anonymous.status_code in {303, 401, 403}
    _login(client, "alice")
    page = client.get("/")
    response = client.post("/availability/query", data={
        "csrf_token": _csrf(page), "venue": "前卫体育馆",
        "sport": "羽毛球", "target_day": "today",
    })
    assert response.status_code == 200
    assert "前卫体育馆" in response.text
    assert "token-alice" not in response.text


def test_service_queries_beijing_date_and_keeps_tokens_separate(web):
    from jlu_booking.web.availability import AvailabilityService

    _client, services = web
    calls = []

    def fake_query(*, query_date, sport_short_name, shop_num, token):
        calls.append((query_date, sport_short_name, shop_num, token))
        return {"placeArray": [{"projectName": {"name": token, "id": 3, "shortname": "ymq3"}, "projectInfo": [{"state": 1, "starttime": "15:30", "endtime": "17:30"}]}]}

    service = AvailabilityService(
        services.credentials, services.throttles, query_func=fake_query
    )
    utc_now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    alice = services.accounts.find_by_username("alice")
    bob = services.accounts.find_by_username("bob")
    alice_result = service.query(alice.id, "前卫体育馆", "羽毛球", "tomorrow", utc_now)
    bob_result = service.query(bob.id, "前卫体育馆", "羽毛球", "today", utc_now)

    assert alice_result.query_date == "2026-09-24"
    assert bob_result.query_date == "2026-09-23"
    assert calls == [
        ("2026-09-24", "ymq", "0002", "token-alice"),
        ("2026-09-23", "ymq", "0002", "token-bob"),
    ]
    assert alice_result.slots[0]["court_name"] == "token-alice"
    assert bob_result.slots[0]["court_name"] == "token-bob"
    with pytest.raises(ValueError):
        service.query(alice.id, "前卫体育馆", "排球", "today", utc_now)
    assert len(calls) == 2


def test_query_rejects_wrong_role_pending_disabled_and_missing_csrf(web):
    from jlu_booking.web.availability import AvailabilityService

    client, services = web
    calls = []
    services.availability = AvailabilityService(
        services.credentials, services.throttles,
        query_func=lambda **kwargs: calls.append(kwargs) or {"placeArray": []},
    )
    form = {"venue": "前卫体育馆", "sport": "羽毛球", "target_day": "today"}
    services.accounts.register_pending(
        "pending", "long password value", source_ip="pending", now=NOW
    )
    _login(client, "pending")
    assert client.post("/availability/query", data=form, follow_redirects=False).status_code == 303
    client.cookies.clear()
    _login(client, "owner")
    assert client.post("/availability/query", data=form, follow_redirects=False).status_code == 404
    client.cookies.clear()
    _login(client, "alice")
    assert client.post("/availability/query", data=form).status_code == 403
    csrf = _csrf(client.get("/"))
    alice = services.accounts.find_by_username("alice")
    services.accounts.disable(alice.id, now=NOW)
    assert client.post(
        "/availability/query", data={**form, "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    assert calls == []


def test_query_limit_is_per_user_and_six_per_minute(web):
    from jlu_booking.web.availability import AvailabilityService

    client, services = web
    calls = []
    services.availability = AvailabilityService(
        services.credentials, services.throttles,
        query_func=lambda **kwargs: calls.append(kwargs["token"]) or {"placeArray": []},
    )
    form = {"venue": "前卫体育馆", "sport": "羽毛球", "target_day": "today"}
    _login(client, "alice")
    csrf = _csrf(client.get("/"))
    for _ in range(6):
        assert client.post("/availability/query", data={**form, "csrf_token": csrf}).status_code == 200
    denied = client.post("/availability/query", data={**form, "csrf_token": csrf})
    assert denied.status_code == 429
    assert len(calls) == 6
    client.cookies.clear()
    _login(client, "bob")
    csrf = _csrf(client.get("/"))
    assert client.post("/availability/query", data={**form, "csrf_token": csrf}).status_code == 200
    assert calls[-1] == "token-bob"


def test_school_query_errors_are_classified_and_never_leak_response(web):
    import requests

    from jlu_booking.api import ServerResponseError
    from jlu_booking.web.availability import AvailabilityQueryError, AvailabilityService

    _client, services = web
    user = services.accounts.find_by_username("alice")
    response = requests.Response()
    response.status_code = 429
    errors = [
        (requests.HTTPError("busy", response=response), "rate_limit"),
        (ServerResponseError({"msg": "Token 已失效"}), "auth"),
        (ServerResponseError({"msg": "token-alice secret raw response"}), "unavailable"),
    ]
    for error, expected_kind in errors:
        calls = []

        def reject(**kwargs):
            calls.append(kwargs)
            raise error

        service = AvailabilityService(
            services.credentials, services.throttles, query_func=reject
        )
        with pytest.raises(AvailabilityQueryError) as raised:
            service.query(user.id, "前卫体育馆", "羽毛球", "today", NOW)
        assert raised.value.kind == expected_kind
        assert "token-alice" not in str(raised.value)
        assert "raw response" not in str(raised.value)
        assert len(calls) == 1


def test_simultaneous_user_queries_keep_owner_credentials(web):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from jlu_booking.web.availability import AvailabilityService

    _client, services = web
    rendezvous = Barrier(2)

    def fake_query(*, token, **_kwargs):
        rendezvous.wait(timeout=2)
        return {"placeArray": [{"projectName": {
            "name": token, "id": 1, "shortname": "ymq1",
        }, "projectInfo": [{"state": 1, "starttime": "15:30", "endtime": "17:30"}]}]}

    service = AvailabilityService(
        services.credentials, services.throttles, query_func=fake_query
    )
    alice = services.accounts.find_by_username("alice")
    bob = services.accounts.find_by_username("bob")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(
            service.query, user.id, "前卫体育馆", "羽毛球", "today", NOW
        ) for user in (alice, bob)]
        results = [future.result(timeout=3) for future in futures]
    assert results[0].slots[0]["court_name"] == "token-alice"
    assert results[1].slots[0]["court_name"] == "token-bob"
