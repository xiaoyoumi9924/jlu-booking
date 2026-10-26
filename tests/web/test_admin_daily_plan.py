"""Administrator control over a user's daily plan and protected companion view."""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.audit import AuditService, ReauthenticationService
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import TaskDraft, TaskService

BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 23, 6, 30, tzinfo=BEIJING)
ADMIN_PASSWORD = "owner password value"


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


@pytest.fixture
def web(tmp_path, monkeypatch):
    for module in (
        "jlu_booking.web.dependencies",
        "jlu_booking.web.routes.auth",
        "jlu_booking.web.routes.admin",
    ):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: NOW, raising=False)
    key = Fernet.generate_key()
    settings = WebSettings(
        data_dir=tmp_path,
        database_path=tmp_path / "web.sqlite3",
        runtime_root=tmp_path / "runtime",
        backup_dir=tmp_path / "backups",
        token_key_file=tmp_path / "token.key",
        blind_key_file=tmp_path / "blind.key",
        token_key=key,
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
        CredentialCipher(key, b"b" * 32),
        throttles,
        token_validator=lambda _token: TokenValidationResult("valid", "ok"),
        companion_validator=lambda _number, _token: {"id": 1, "name": "同学"},
    )
    services = AppServices(
        connection, passwords, sessions, throttles, accounts, credentials,
        TaskService(connection), AuditService(connection),
        ReauthenticationService(connection, passwords, throttles),
    )
    with TestClient(create_app(settings, services)) as client:
        yield client, services
    connection.close()


def _create_user(services, username="alice", token="private-token"):
    user = services.accounts.register_pending(
        username, "long password value", source_ip=username, now=NOW
    )
    services.credentials.activate_user(user.id, token, now=NOW)
    services.credentials.save_companion(user.id, "20260001", now=NOW)
    return services.accounts.get(user.id)


def _save_plan(services, user, *, court=3, mode="scan", target_day="tomorrow"):
    companion = services.credentials.decrypt_companion(user.id)
    return services.daily_plans.save(user.id, TaskDraft(
        target_day, "前卫体育馆", "羽毛球", companion.id, court,
        [["19:30", "21:30"], ["17:30", "19:30"]], mode == "real",
    ), now=NOW)


def _login(client, username, password):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _admin(client, services):
    admin = services.accounts.create_admin("owner", ADMIN_PASSWORD, now=NOW)
    _login(client, "owner", ADMIN_PASSWORD)
    return admin


def _form(**changes):
    data = {
        "target_day": "tomorrow",
        "venue": "前卫体育馆",
        "sport": "羽毛球",
        "preferred_court_number": "3",
        "priority": ["15:30|17:30", "17:30|19:30"],
        "mode": "scan",
        "reason": "用户申请调整时间优先级",
    }
    data.update(changes)
    return data


def _audit_rows(services, action):
    return [
        row for row in services.connection.execute(
            "SELECT * FROM audit_events WHERE action=? ORDER BY id", (action,)
        )
    ]


def test_user_detail_masks_companion_and_shows_plan(web):
    client, services = web
    user = _create_user(services)
    _save_plan(services, user, court=8)
    services.daily_plans.set_enabled(user.id, True, now=NOW)
    _admin(client, services)
    page = client.get(f"/admin/users/{user.id}")
    assert page.status_code == 200
    assert "每日自动预约" in page.text
    assert "已开启" in page.text
    assert "前卫体育馆 · 羽毛球" in page.text
    assert "8 号场" in page.text
    assert "学号" in page.text
    assert "同*" in page.text
    assert "同学" not in page.text
    assert "20260001" not in page.text
    assert 'id="admin-plan-edit"' in page.text
    assert 'id="admin-plan-disable"' in page.text
    assert 'data-companion-open' in page.text
    assert "2026-09-23 06:30" in page.text


def test_admin_save_plan_is_audited_and_unchanged_is_skipped(web):
    client, services = web
    user = _create_user(services)
    admin = _admin(client, services)
    page = client.get(f"/admin/users/{user.id}")
    response = client.post(
        f"/admin/users/{user.id}/daily-plan",
        data={"csrf_token": _csrf(page), **_form()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("?notice=saved")
    plan = services.daily_plans.get_for_user(user.id)
    assert plan is not None and plan.preferred_court_number == 3
    assert plan.updated_by_user_id == admin.id
    events = _audit_rows(services, "daily_plan_updated")
    assert len(events) == 1
    metadata = json.loads(events[0]["metadata_json"])
    assert metadata["reason"] == "用户申请调整时间优先级"
    assert metadata["changed_fields"] == "created"
    assert metadata["before"] == ""
    assert "20260001" not in events[0]["metadata_json"]

    again = client.post(
        f"/admin/users/{user.id}/daily-plan",
        data={"csrf_token": _csrf(client.get(f"/admin/users/{user.id}")), **_form(reason="再次保存")},
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert again.headers["location"].endswith("?notice=unchanged")
    assert len(_audit_rows(services, "daily_plan_updated")) == 1

    updated = client.post(
        f"/admin/users/{user.id}/daily-plan",
        data={"csrf_token": _csrf(client.get(f"/admin/users/{user.id}")), **_form(
            preferred_court_number="8", mode="real", reason="改为真实预约",
        )},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    plan = services.daily_plans.get_for_user(user.id)
    assert plan.preferred_court_number == 8 and plan.real_booking_enabled is True
    metadata = json.loads(_audit_rows(services, "daily_plan_updated")[-1]["metadata_json"])
    assert metadata["changed_fields"] == "preferred_court_number,real_booking_enabled"
    assert '"preferred_court_number":8' in metadata["after"]
    assert '"preferred_court_number":3' in metadata["before"]


def test_admin_enable_and_disable_cancel_scheduled_tasks(web):
    client, services = web
    user = _create_user(services)
    admin = _admin(client, services)
    _save_plan(services, user)
    page = client.get(f"/admin/users/{user.id}")
    enabled = client.post(
        f"/admin/users/{user.id}/daily-plan/enable",
        data={"csrf_token": _csrf(page), "reason": "用户申请补开"},
        follow_redirects=False,
    )
    assert enabled.status_code == 303
    assert enabled.headers["location"].endswith("?notice=enabled")
    plan = services.daily_plans.get_for_user(user.id)
    assert plan.enabled is True
    assert plan.enabled_by_user_id == admin.id
    assert plan.enabled_at is not None
    task = services.connection.execute(
        "SELECT id, status, source FROM booking_tasks WHERE user_id=?", (user.id,)
    ).fetchone()
    assert task is not None and task["source"] == "daily"
    assert task["status"] == "scheduled"
    events = _audit_rows(services, "daily_plan_enabled")
    assert len(events) == 1
    assert json.loads(events[0]["metadata_json"])["reason"] == "用户申请补开"

    page = client.get(f"/admin/users/{user.id}")
    disabled = client.post(
        f"/admin/users/{user.id}/daily-plan/disable",
        data={"csrf_token": _csrf(page), "reason": "用户申请暂停"},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    assert disabled.headers["location"].endswith("?notice=disabled")
    assert services.daily_plans.get_for_user(user.id).enabled is False
    assert services.connection.execute(
        "SELECT status FROM booking_tasks WHERE id=?", (task["id"],)
    ).fetchone()[0] == "cancelled"
    metadata = json.loads(_audit_rows(services, "daily_plan_disabled")[-1]["metadata_json"])
    assert metadata["cancelled_task_ids"] == str(task["id"])
    assert metadata["reason"] == "用户申请暂停"


def test_admin_cannot_enable_real_plan_without_explicit_confirmation(web):
    client, services = web
    user = _create_user(services)
    _admin(client, services)
    _save_plan(services, user, mode="real")
    page = client.get(f"/admin/users/{user.id}")
    blocked = client.post(
        f"/admin/users/{user.id}/daily-plan/enable",
        data={"csrf_token": _csrf(page), "reason": "用户申请补开"},
    )
    assert blocked.status_code == 400
    assert "请勾选确认" in blocked.text
    assert services.daily_plans.get_for_user(user.id).enabled is False
    assert _audit_rows(services, "daily_plan_enabled") == []

    allowed = client.post(
        f"/admin/users/{user.id}/daily-plan/enable",
        data={"csrf_token": _csrf(client.get(f"/admin/users/{user.id}")),
              "reason": "用户申请补开", "confirm_real": "yes"},
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    assert services.daily_plans.get_for_user(user.id).enabled is True


def test_admin_mutations_require_reason_and_csrf(web):
    client, services = web
    user = _create_user(services)
    _admin(client, services)
    page = client.get(f"/admin/users/{user.id}")
    missing_reason = client.post(
        f"/admin/users/{user.id}/daily-plan",
        data={"csrf_token": _csrf(page), **_form(reason="   ")},
    )
    assert missing_reason.status_code == 400
    assert "请填写操作原因" in missing_reason.text
    assert services.daily_plans.get_for_user(user.id) is None
    bad_csrf = client.post(
        f"/admin/users/{user.id}/daily-plan",
        data={"csrf_token": "bad", **_form()},
    )
    assert bad_csrf.status_code == 403
    assert services.daily_plans.get_for_user(user.id) is None


def test_companion_reveal_requires_admin_password_and_is_audited(web):
    client, services = web
    user = _create_user(services)
    _admin(client, services)
    page = client.get(f"/admin/users/{user.id}")
    url = f"/admin/users/{user.id}/companion/reveal"
    missing = client.post(url, data={"csrf_token": _csrf(page)})
    assert missing.status_code == 403
    wrong = client.post(url, data={"csrf_token": _csrf(page), "password": "wrong password"})
    assert wrong.status_code == 403
    assert "20260001" not in wrong.text
    assert "同学" not in wrong.text

    revealed = client.post(
        url, data={"csrf_token": _csrf(client.get(f"/admin/users/{user.id}")),
                   "password": ADMIN_PASSWORD},
    )
    assert revealed.status_code == 200
    assert revealed.json() == {
        "name": "同学", "student_number": "20260001", "hide_after": 60,
    }
    assert revealed.headers["cache-control"].startswith("no-store")
    event = _audit_rows(services, "companion_revealed")[-1]
    assert "20260001" not in event["metadata_json"]
    assert "同学" not in event["metadata_json"]
    assert json.loads(event["metadata_json"])["fields"] == "name,student_id"

    without_password = client.post(url, data={"csrf_token": _csrf(client.get(f"/admin/users/{user.id}"))})
    assert without_password.status_code == 403


def test_ordinary_user_cannot_manage_another_plan_or_companion(web):
    client, services = web
    alice = _create_user(services)
    bob = _create_user(services, "bob", "bob-token")
    _login(client, "alice", "long password value")
    assert client.get(f"/admin/users/{bob.id}").status_code == 404
    assert client.post(
        f"/admin/users/{bob.id}/daily-plan", data=_form()
    ).status_code == 404
    assert client.post(
        f"/admin/users/{bob.id}/daily-plan/enable", data={"reason": "x"}
    ).status_code == 404
    assert client.post(
        f"/admin/users/{bob.id}/companion/reveal", data={"password": ADMIN_PASSWORD}
    ).status_code == 404
