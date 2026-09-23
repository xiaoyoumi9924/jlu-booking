import re
from datetime import datetime
from pathlib import Path
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


BEIJING = ZoneInfo("Asia/Shanghai")


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


@pytest.fixture
def web(tmp_path):
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
        token_validator=lambda _token: TokenValidationResult("valid", "accepted"),
        companion_validator=lambda student, token: {
            "id": 42,
            "name": "测试同学",
        }
        if student == "20260001" and token.startswith("token-")
        else (_ for _ in ()).throw(ValueError("invalid")),
    )
    services = AppServices(
        connection=connection,
        passwords=passwords,
        sessions=sessions,
        throttles=throttles,
        accounts=accounts,
        credentials=credentials,
    )
    app = create_app(settings, services)
    with TestClient(app) as client:
        yield client, services
    connection.close()


def _register(client, username="alice", password="long password value"):
    csrf = _csrf(client.get("/register"))
    return client.post(
        "/register",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def _login(client, username="alice", password="long password value"):
    csrf = _csrf(client.get("/login"))
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def _register_and_login_pending(client, username="alice"):
    assert _register(client, username).status_code == 303
    response = _login(client, username)
    assert response.status_code == 303
    return response


def _activate(client, token="token-alice"):
    page = client.get("/onboarding/token")
    return client.post(
        "/onboarding/token",
        data={"token": token, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_registration_login_and_pending_profile_redirect(web):
    client, _ = web
    response = _register_and_login_pending(client)
    assert response.headers["location"] == "/onboarding/token"
    profile = client.get("/profile", follow_redirects=False)
    assert profile.status_code == 303
    assert profile.headers["location"] == "/onboarding/token"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie


def test_token_onboarding_activates_without_echoing_token(web):
    client, services = web
    _register_and_login_pending(client)
    response = _activate(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "token-alice" not in response.text
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "token-alice" not in profile.text
    assert "toke***lice" in profile.text
    assert services.accounts.find_by_username("alice").status == "active"


def test_token_onboarding_full_capacity_returns_conflict(web):
    client, services = web
    _register_and_login_pending(client)
    services.credentials._user_limit = 0

    response = _activate(client)

    assert response.status_code == 409
    assert services.accounts.find_by_username("alice").status == "pending_token"


def test_companion_save_requires_csrf_and_never_echoes_number(web):
    client, _ = web
    _register_and_login_pending(client)
    _activate(client)
    rejected = client.post(
        "/profile/companion", data={"student_number": "20260001"}
    )
    assert rejected.status_code == 403
    page = client.get("/profile")
    saved = client.post(
        "/profile/companion",
        data={"student_number": "20260001", "csrf_token": _csrf(page)},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert "20260001" not in saved.text
    profile = client.get("/profile")
    assert "20260001" not in profile.text
    assert "2******1" in profile.text


def test_login_failure_is_generic_and_does_not_echo_password(web):
    client, _ = web
    _register(client)
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "wrong private password",
            "csrf_token": _csrf(page),
        },
    )
    assert response.status_code == 400
    assert "用户名或密码错误" in response.text
    assert "wrong private password" not in response.text


def test_logout_requires_csrf_and_revokes_session(web):
    client, _ = web
    _register_and_login_pending(client)
    assert client.post("/logout").status_code == 403
    page = client.get("/onboarding/token")
    response = client.post(
        "/logout", data={"csrf_token": _csrf(page)}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/profile", follow_redirects=False).headers["location"] == "/login"


def test_forced_password_change_rotates_session(web):
    client, services = web
    _register_and_login_pending(client)
    user = services.accounts.find_by_username("alice")
    services.connection.execute(
        "UPDATE users SET must_change_password = 1 WHERE id = ?", (user.id,)
    )
    assert client.get("/profile", follow_redirects=False).headers["location"] == (
        "/change-password"
    )
    page = client.get("/change-password")
    before = client.cookies.get("jlu_session")
    response = client.post(
        "/change-password",
        data={
            "current_password": "long password value",
            "new_password": "new long password value",
            "csrf_token": _csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.cookies.get("jlu_session") != before
    assert "long password value" not in response.text


def test_every_response_has_security_headers_and_private_pages_no_store(web):
    client, _ = web
    public = client.get("/login")
    assert "default-src 'self'" in public.headers["content-security-policy"]
    assert public.headers["x-frame-options"] == "DENY"
    assert public.headers["x-content-type-options"] == "nosniff"
    _register_and_login_pending(client)
    private = client.get("/onboarding/token")
    assert private.headers["cache-control"] == "no-store"
    raw_session = client.cookies.get("jlu_session")
    assert raw_session not in private.text


def test_anonymous_state_changes_reject_missing_csrf(web):
    client, _ = web
    response = client.post(
        "/register", data={"username": "alice", "password": "long password value"}
    )
    assert response.status_code == 403


def test_login_rate_limit_returns_429_with_retry_after(web):
    client, _ = web
    _register(client)
    page = client.get("/login")
    token = _csrf(page)
    for _ in range(10):
        assert client.post(
            "/login",
            data={"username": "alice", "password": "wrong password value", "csrf_token": token},
        ).status_code == 400
    response = client.post(
        "/login",
        data={"username": "alice", "password": "wrong password value", "csrf_token": token},
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
