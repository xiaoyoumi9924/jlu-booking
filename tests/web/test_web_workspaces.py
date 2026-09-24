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
from jlu_booking.web.tasks import TaskDraft, TaskService


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
        "jlu_booking.web.routes.profile",
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
        companion_validator=lambda _number, _token: {"id": 1, "name": "测试同学"},
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


def test_user_workspace_uses_gui_brand_and_keeps_admin_separate(workspace):
    user_client, admin_client, _services = workspace
    _login(user_client, "alice", "long password value")
    _login(admin_client, "owner", "owner password value")

    html = user_client.get("/").text
    assert 'class="workspace-brand"' in html
    assert "JILIN UNIVERSITY" in html
    assert "服务场馆" in html
    assert 'data-user-nav' in html and 'data-admin-nav' not in html
    assert 'class="topbar"' not in html
    assert 'class="workspace-head"' in html
    assert 'action="/logout"' in html
    assert 'href="/?venue=' in html
    admin_html = admin_client.get("/admin").text
    assert 'class="workspace-brand"' not in admin_html
    assert 'class="topbar"' in admin_html


def test_venue_link_reloads_matching_sport_choices(workspace):
    user_client, _admin_client, _services = workspace
    _login(user_client, "alice", "long password value")

    second = user_client.get("/?venue=宋治平体育馆")
    assert second.status_code == 200
    assert 'data-selected-venue="宋治平体育馆"' in second.text
    assert 'data-select-sport="排球"' in second.text
    assert 'data-select-sport="羽毛球"' not in second.text
    assert 'name="venue"' in second.text
    assert 'name="sport"' in second.text
    assert 'name="target_day"' in second.text
    assert 'data-select-day="today"' in second.text
    assert 'data-select-day="tomorrow"' in second.text
    assert 'data-clear-results' in second.text

    invalid = user_client.get("/?venue=invalid")
    assert invalid.status_code == 200
    assert 'data-selected-venue="前卫体育馆"' in invalid.text


def test_user_pages_share_gui_shell_with_distinct_page_titles(workspace):
    user_client, admin_client, _services = workspace
    _login(user_client, "alice", "long password value")
    _login(admin_client, "owner", "owner password value")
    for path, title, form_action in (
        ("/", "场地预约查询", "/availability/query"),
        ("/tasks/new", "自动预约", "/tasks/new"),
        ("/daily-plan", "每日自动预约", "/daily-plan"),
        ("/profile", "个人中心", "/profile/token"),
    ):
        page = user_client.get(path)
        assert page.status_code == 200
        assert 'class="workspace-head"' in page.text
        assert f"<h1>{title}</h1>" in page.text
        assert 'href="/profile"' in page.text
        assert f'action="{form_action}"' in page.text
        assert 'data-user-nav' in page.text
    admin = admin_client.get("/admin")
    assert 'data-admin-nav' in admin.text
    assert 'data-user-nav' not in admin.text


def test_personal_center_shows_only_owned_booking_history_and_missing_log(workspace):
    user_client, admin_client, services = workspace
    alice = services.accounts.find_by_username("alice")
    companion = services.credentials.save_companion(alice.id, "20260001", now=NOW)
    task = services.tasks.create(alice.id, TaskDraft(
        "tomorrow", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    services.connection.execute("UPDATE booking_tasks SET status='cancelled' WHERE id=?", (task.id,))
    services.connection.execute(
        "INSERT INTO manual_candidates (id,user_id,venue,sport,query_date,court_name,"
        "place_short_name,start_time,end_time,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("candidate-alice", alice.id, "前卫体育馆", "羽毛球", "2026-09-24", "1号场",
         "ymq1", "15:30", "17:30", NOW.isoformat(), NOW.isoformat()),
    )
    services.connection.execute(
        "INSERT INTO manual_booking_attempts (id,user_id,candidate_id,companion_id,"
        "companion_updated_at,school_companion_id,credential_updated_at,"
        "confirmation_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("attempt-alice", alice.id, "candidate-alice", companion.id, NOW.isoformat(), "1",
         NOW.isoformat(), b"alice-attempt", "rejected", NOW.isoformat(), NOW.isoformat()),
    )
    bob = services.accounts.register_pending("bob", "another long password", source_ip="bob", now=NOW)
    services.credentials.activate_user(bob.id, "private-token-bob", now=NOW)
    bob_companion = services.credentials.save_companion(bob.id, "20260002", now=NOW)
    bob_task = services.tasks.create(bob.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", bob_companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    services.connection.execute(
        "INSERT INTO manual_candidates (id,user_id,venue,sport,query_date,court_name,"
        "place_short_name,start_time,end_time,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("candidate-bob", bob.id, "前卫体育馆", "羽毛球", "2026-09-24", "2号场",
         "ymq2", "15:30", "17:30", NOW.isoformat(), NOW.isoformat()),
    )
    services.connection.execute(
        "INSERT INTO manual_booking_attempts (id,user_id,candidate_id,companion_id,"
        "companion_updated_at,school_companion_id,credential_updated_at,"
        "confirmation_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("attempt-bob", bob.id, "candidate-bob", bob_companion.id, NOW.isoformat(), "1",
         NOW.isoformat(), b"bob-attempt", "rejected", NOW.isoformat(), NOW.isoformat()),
    )
    _login(user_client, "alice", "long password value")
    page = user_client.get("/profile")
    assert "我的预约记录" in page.text
    assert f'href="/tasks/{task.id}"' in page.text
    assert 'href="/manual/result/attempt-alice"' in page.text
    assert f'href="/tasks/{bob_task.id}"' not in page.text
    assert 'href="/manual/result/attempt-bob"' not in page.text
    assert "alice-private-token" not in page.text
    assert user_client.get(f"/tasks/{bob_task.id}").status_code == 404
    assert user_client.get("/manual/result/attempt-bob").status_code == 404
    assert user_client.get(f"/manual/result/attempt-alice").status_code == 200
    assert "日志暂不可用" in user_client.get(f"/tasks/{task.id}").text
    _login(admin_client, "owner", "owner password value")
    admin_profile = admin_client.get("/profile", follow_redirects=False)
    assert admin_profile.status_code == 303
    assert admin_profile.headers["location"] == "/admin"


def test_personal_center_history_paginates_and_disabled_user_cannot_read(workspace):
    client, _admin_client, services = workspace
    alice = services.accounts.find_by_username("alice")
    companion = services.credentials.save_companion(alice.id, "20260001", now=NOW)
    for index in range(21):
        task = services.tasks.create(alice.id, TaskDraft(
            "today", "前卫体育馆", "羽毛球", companion.id, 3,
            [["15:30", "17:30"]], False,
        ), now=NOW)
        services.connection.execute(
            "UPDATE booking_tasks SET status='cancelled',created_at=? WHERE id=?",
            (f"2026-09-23T06:{index:02d}:00+08:00", task.id),
        )
    _login(client, "alice", "long password value")
    first = client.get("/profile")
    second = client.get("/profile?page=2")
    assert 'href="/profile?page=2"' in first.text
    assert 'href="/profile?page=1"' in second.text
    assert second.text.count('>查看详情</a>') == 1
    assert client.get("/profile?page=0").status_code == 422
    services.accounts.disable(alice.id, now=NOW)
    assert client.get("/profile", follow_redirects=False).headers["location"] == "/login"
