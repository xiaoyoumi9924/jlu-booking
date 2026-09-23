"""Daily controls are private and never contact the school API."""

import re
from datetime import datetime
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

NOW = datetime(2026, 9, 23, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def login(client, username, password="long password value"):
    page = client.get("/login")
    return client.post("/login", data={"username": username, "password": password,
                                        "csrf_token": csrf(page)}, follow_redirects=False)


def form_data(token, **changes):
    data = {"csrf_token": token, "target_day": "tomorrow", "venue": "前卫体育馆",
            "sport": "羽毛球", "preferred_court_number": "3",
            "priority": ["15:30|17:30", "17:30|19:30"], "mode": "scan"}
    data.update(changes)
    return data


@pytest.fixture
def web(tmp_path, monkeypatch):
    for module in ("jlu_booking.web.dependencies", "jlu_booking.web.routes.auth",
                   "jlu_booking.web.routes.dashboard", "jlu_booking.web.routes.daily_plans"):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: NOW, raising=False)
    key = Fernet.generate_key()
    settings = WebSettings(
        data_dir=tmp_path, database_path=tmp_path / "web.sqlite3",
        runtime_root=tmp_path / "runtime", backup_dir=tmp_path / "backups",
        token_key_file=tmp_path / "token.key", blind_key_file=tmp_path / "blind.key",
        token_key=key, blind_key=b"b" * 32, cookie_secure=False,
    )
    db = connect_database(settings.database_path)
    migrate_database(db)
    passwords = PasswordService(); sessions = SessionService(db); throttles = ThrottleService(db)
    accounts = AccountService(db, passwords, sessions, throttles)
    credentials = CredentialService(
        db, CredentialCipher(key, b"b" * 32), throttles,
        token_validator=lambda _: TokenValidationResult("valid", "ok"),
        companion_validator=lambda number, _: {"id": 1, "name": "同学"},
    )
    services = AppServices(db, passwords, sessions, throttles, accounts, credentials,
                           TaskService(db))
    alice = accounts.register_pending("alice", "long password value", source_ip="alice", now=NOW)
    credentials.activate_user(alice.id, "alice-test-token", now=NOW)
    credentials.save_companion(alice.id, "20260001", now=NOW)
    accounts.register_pending("pending", "long password value", source_ip="pending", now=NOW)
    accounts.create_admin("admin", "long password value", now=NOW)
    with TestClient(create_app(settings, services)) as client:
        yield client, services, alice.id
    db.close()


def test_daily_plan_form_and_toggle_materialize_and_cancel(web):
    client, services, alice_id = web
    login(client, "alice")
    page = client.get("/daily-plan")
    assert page.status_code == 200
    assert 'value="scan"' in page.text and 'value="real"' in page.text
    assert 'data-venue="宋治平体育馆"' in page.text
    assert '2026-09-23' in page.text
    assert "未开启" in page.text
    saved = client.post("/daily-plan", data=form_data(csrf(page)), follow_redirects=False)
    assert saved.status_code == 303
    assert '2026-09-24' in client.get("/daily-plan").text
    plan = services.daily_plans.get_for_user(alice_id)
    assert plan is not None and plan.enabled is False and plan.real_booking_enabled is False
    enabled = client.post("/daily-plan/enable", data={"csrf_token": csrf(client.get("/daily-plan"))}, follow_redirects=False)
    assert enabled.status_code == 303
    task = services.connection.execute(
        "SELECT id,status,source,target_day FROM booking_tasks WHERE user_id=?", (alice_id,)
    ).fetchone()
    assert tuple(task)[1:] == ("scheduled", "daily", "tomorrow")
    disabled = client.post("/daily-plan/disable", data={"csrf_token": csrf(client.get("/daily-plan"))}, follow_redirects=False)
    assert disabled.status_code == 303
    assert services.connection.execute("SELECT status FROM booking_tasks WHERE id=?", (task["id"],)).fetchone()[0] == "cancelled"
    assert services.daily_plans.get_for_user(alice_id).enabled is False
    without_confirmation = client.post(
        "/daily-plan", data=form_data(csrf(client.get("/daily-plan")), mode="real")
    )
    assert without_confirmation.status_code == 400
    real = client.post("/daily-plan", data=form_data(
        csrf(client.get("/daily-plan")), mode="real", confirm_real="yes"
    ), follow_redirects=False)
    assert real.status_code == 303
    assert services.daily_plans.get_for_user(alice_id).real_booking_enabled is True
    no_ack = client.post("/daily-plan/enable", data={"csrf_token": csrf(client.get("/daily-plan"))})
    assert no_ack.status_code == 400
    assert services.daily_plans.get_for_user(alice_id).enabled is False
    yes_ack = client.post("/daily-plan/enable", data={
        "csrf_token": csrf(client.get("/daily-plan")), "confirm_real": "yes",
    }, follow_redirects=False)
    assert yes_ack.status_code == 303
    assert services.daily_plans.get_for_user(alice_id).enabled is True


def test_daily_plan_access_csrf_and_mutation_throttle(web):
    client, services, alice_id = web
    assert client.get("/daily-plan", follow_redirects=False).status_code == 303
    login(client, "pending")
    assert client.get("/daily-plan", follow_redirects=False).status_code == 303
    assert client.post("/daily-plan/enable", data={}, follow_redirects=False).status_code == 303
    client.cookies.clear()
    login(client, "admin")
    assert client.get("/daily-plan").status_code == 404
    assert client.post("/daily-plan/enable", data={}).status_code == 404
    client.cookies.clear()
    login(client, "alice")
    assert client.post("/daily-plan", data=form_data("bad")).status_code == 403
    token = csrf(client.get("/daily-plan"))
    for _ in range(20):
        assert client.post("/daily-plan", data=form_data(token), follow_redirects=False).status_code == 303
    assert client.post("/daily-plan", data=form_data(token)).status_code == 429
    services.accounts.disable(alice_id, now=NOW)
    assert client.get("/daily-plan", follow_redirects=False).status_code == 303


def test_disabling_after_cutoff_does_not_stop_running_daily_task(web, monkeypatch):
    client, services, alice_id = web
    login(client, "alice")
    page = client.get("/daily-plan")
    client.post("/daily-plan", data=form_data(csrf(page)))
    client.post("/daily-plan/enable", data={"csrf_token": csrf(client.get("/daily-plan"))})
    services.connection.execute("UPDATE booking_tasks SET status='running' WHERE user_id=?", (alice_id,))
    cutoff = NOW.replace(hour=7, minute=27, second=1)
    monkeypatch.setattr("jlu_booking.web.routes.daily_plans.now_beijing", lambda: cutoff)
    response = client.post("/daily-plan/disable", data={"csrf_token": csrf(client.get("/daily-plan"))}, follow_redirects=False)
    assert response.status_code == 303
    assert services.connection.execute("SELECT status FROM booking_tasks WHERE user_id=?", (alice_id,)).fetchone()[0] == "running"
    detail = client.get("/daily-plan")
    assert "2026-09-24" in detail.text
    assert "当前任务仍在运行" in detail.text
    dashboard = client.get("/")
    assert "当前任务仍在运行" in dashboard.text
