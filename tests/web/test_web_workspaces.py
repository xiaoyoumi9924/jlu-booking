"""Role-specific authenticated entry points and navigation."""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.api import VENUES
from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import TaskService


NOW = datetime(2026, 9, 23, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def _csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for module in (
        "jlu_booking.web.dependencies",
        "jlu_booking.web.routes.auth",
        "jlu_booking.web.routes.dashboard",
        "jlu_booking.web.routes.admin",
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
    user = accounts.register_pending(
        "alice", "long password value", source_ip="alice", now=NOW
    )
    credentials.activate_user(user.id, "alice-private-token", now=NOW)
    accounts.create_admin("owner", "owner password value", now=NOW)
    app = create_app(settings, services)
    with TestClient(app) as user_client, TestClient(app) as admin_client:
        yield user_client, admin_client, services
    connection.close()


def _login(client, username, password):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_login_routes_each_role_to_its_own_workspace(workspace):
    user_client, admin_client, _services = workspace
    user_login = _login(user_client, "alice", "long password value")
    admin_login = _login(admin_client, "owner", "owner password value")

    assert user_login.headers["location"] == "/"
    assert admin_login.headers["location"] == "/admin"
    assert admin_client.get("/", follow_redirects=False).headers["location"] == "/admin"


def test_roles_have_distinct_navigation_and_user_cannot_open_admin(workspace):
    user_client, admin_client, _services = workspace
    _login(user_client, "alice", "long password value")
    _login(admin_client, "owner", "owner password value")

    user_page = user_client.get("/")
    assert user_page.status_code == 200
    assert "data-user-nav" in user_page.text
    assert "data-admin-nav" not in user_page.text
    for path in ("/admin", "/admin/users", "/admin/tasks", "/admin/audit"):
        assert user_client.get(path).status_code == 404
        admin_page = admin_client.get(path)
        assert admin_page.status_code == 200
        assert "data-admin-nav" in admin_page.text


def test_newly_activated_user_lands_in_workspace(workspace):
    user_client, _admin_client, services = workspace
    services.accounts.register_pending(
        "bob", "another long password", source_ip="bob", now=NOW
    )
    login = _login(user_client, "bob", "another long password")
    assert login.headers["location"] == "/onboarding/token"
    onboarding = user_client.get("/onboarding/token")

    response = user_client.post(
        "/onboarding/token",
        data={"token": "bob-private-token", "csrf_token": _csrf(onboarding)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_workspace_shows_gui_venue_choices_and_responsive_navigation(workspace):
    user_client, admin_client, _services = workspace
    _login(user_client, "alice", "long password value")
    _login(admin_client, "owner", "owner password value")
    html = user_client.get("/").text
    assert "未绑定" not in html
    assert "alice-private-token" not in html
    assert all(name in html for name in VENUES)
    for venue_name, info in VENUES.items():
        for sport in info["sports"]:
            assert f'data-venue="{venue_name}"' in html
            assert f'value="{sport}"' in html
    assert 'name="target_day"' in html
    assert 'value="today"' in html and 'value="tomorrow"' in html
    assert 'data-query-results' in html
    assert '/tasks/new' in html
    assert '<details' in html and '<summary' in html
    admin_html = admin_client.get("/admin").text
    assert 'data-admin-nav' in admin_html
    assert '/admin/users' in admin_html and '/admin/tasks' in admin_html
    assert '/admin/audit' in admin_html
    assert '/availability/query' not in admin_html
    css = (Path(__file__).parents[2] / "jlu_booking/web/static/app.css").read_text()
    assert '@media (max-width: 600px)' in css
    assert 'overflow-x' in css
