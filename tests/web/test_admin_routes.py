import json
import re
from datetime import datetime, timedelta
from pathlib import Path
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
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=BEIJING)


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
        companion_validator=lambda _number, _token: {"id": 1, "name": "同学"},
    )
    audit = AuditService(connection)
    reauth = ReauthenticationService(connection, passwords, throttles)
    services = AppServices(
        connection, passwords, sessions, throttles, accounts, credentials,
        TaskService(connection), audit, reauth,
    )
    with TestClient(create_app(settings, services)) as client:
        yield client, services
    connection.close()


def _create_active(services, username="alice", token="private-token"):
    user = services.accounts.register_pending(
        username, "long password value", source_ip=username, now=NOW
    )
    services.credentials.activate_user(user.id, token, now=NOW)
    services.credentials.save_companion(user.id, "20260001", now=NOW)
    return services.accounts.get(user.id)


def _login(client, username, password):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _admin_login(client, services):
    admin = services.accounts.create_admin(
        "owner", "owner password value", now=NOW
    )
    _login(client, "owner", "owner password value")
    return admin


@pytest.mark.parametrize("path", ["/admin", "/admin/users", "/admin/tasks", "/admin/audit", "/admin/logs", "/admin/exceptions", "/admin/stats", "/admin/monitor", "/admin/settings"])
def test_ordinary_user_cannot_open_admin_pages_or_infer_ids(web, path):
    client, services = web
    _create_active(services)
    _login(client, "alice", "long password value")
    assert client.get(path).status_code == 404
    assert client.post("/admin/users/999/token/reveal").status_code == 404


def test_admin_lists_users_and_counts(web):
    client, services = web
    _create_active(services)
    _admin_login(client, services)
    page = client.get("/admin/users")
    assert page.status_code == 200
    assert "alice" in page.text
    assert "priv*****oken" in page.text
    assert "private-token" not in page.text


def test_admin_new_views_are_source_backed_and_invitation_removed(web):
    client, services = web
    user = _create_active(services)
    companion = services.credentials.decrypt_companion(user.id)
    task = services.tasks.create(user.id, TaskDraft(
        "tomorrow", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    services.connection.execute(
        "UPDATE booking_tasks SET status='error' WHERE id=?", (task.id,),
    )
    _admin_login(client, services)
    for path, marker in (
        ("/admin/logs", "暂无运行日志"),
        ("/admin/exceptions", "运行出错"),
        ("/admin/stats", "近七天创建任务"),
        ("/admin/monitor", "当前任务"),
        ("/admin/settings", "容量配置"),
        (f"/admin/users/{user.id}", "预约必备信息"),
    ):
        page = client.get(path)
        assert page.status_code == 200, path
        assert marker in page.text, path
        assert "邀请管理" not in page.text
        assert "private-token" not in page.text
        assert "20260001" not in page.text
    filtered = client.get("/admin/tasks?status=error")
    assert "运行出错" in filtered.text
    assert "前卫体育馆" in filtered.text
    assert "邀请管理" not in filtered.text
    assert "没有符合条件的任务" in client.get("/admin/tasks?status=success").text
    assert "没有符合条件的用户" in client.get("/admin/users?q=does-not-exist").text


def test_token_reveal_requires_recent_admin_password(web):
    client, services = web
    active = _create_active(services)
    _admin_login(client, services)
    page = client.get("/admin/users")
    response = client.post(
        f"/admin/users/{active.id}/token/reveal",
        data={"csrf_token": _csrf(page)},
    )
    assert response.status_code == 403


def test_revealed_token_is_temporary_no_store_and_audited(web):
    client, services = web
    active = _create_active(services)
    _admin_login(client, services)
    page = client.get("/admin/reauth")
    granted = client.post(
        "/admin/reauth",
        data={"password": "owner password value", "csrf_token": _csrf(page)},
        follow_redirects=False,
    )
    assert granted.status_code == 303
    users = client.get("/admin/users")
    response = client.post(
        f"/admin/users/{active.id}/token/reveal",
        data={"csrf_token": _csrf(users)},
    )
    assert response.json() == {"token": "private-token", "hide_after": 30}
    assert response.headers["cache-control"].startswith("no-store")
    event = services.connection.execute(
        "SELECT action, metadata_json FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event["action"] == "token_revealed"
    assert "private-token" not in event["metadata_json"]


def test_reauthentication_expires_after_five_minutes(web, monkeypatch):
    client, services = web
    active = _create_active(services)
    _admin_login(client, services)
    page = client.get("/admin/reauth")
    client.post(
        "/admin/reauth",
        data={"password": "owner password value", "csrf_token": _csrf(page)},
    )
    later = NOW + timedelta(minutes=5, microseconds=1)
    monkeypatch.setattr("jlu_booking.web.routes.admin.now_beijing", lambda: later)
    users = client.get("/admin/users")
    response = client.post(
        f"/admin/users/{active.id}/token/reveal",
        data={"csrf_token": _csrf(users)},
    )
    assert response.status_code == 403


def test_admin_disable_enable_reset_and_pending_delete_are_audited(web):
    client, services = web
    active = _create_active(services)
    pending = services.accounts.register_pending(
        "pending", "long password value", source_ip="pending", now=NOW
    )
    _admin_login(client, services)
    users = client.get("/admin/users")
    csrf = _csrf(users)
    assert client.post(
        f"/admin/users/{active.id}/disable", data={"csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    assert services.accounts.get(active.id).status == "disabled"
    users = client.get("/admin/users")
    assert client.post(
        f"/admin/users/{active.id}/enable", data={"csrf_token": _csrf(users)},
        follow_redirects=False,
    ).status_code == 303
    users = client.get("/admin/users")
    reset = client.post(
        f"/admin/users/{active.id}/reset-password",
        data={"csrf_token": _csrf(users)},
    )
    temporary = reset.json()["temporary_password"]
    assert len(temporary) == 16
    assert temporary not in json.dumps([
        dict(row) for row in services.connection.execute("SELECT * FROM audit_events")
    ])
    users = client.get("/admin/users")
    assert client.post(
        f"/admin/users/{pending.id}/delete", data={"csrf_token": _csrf(users)},
        follow_redirects=False,
    ).status_code == 303
    assert services.accounts.find_by_username("pending") is None
    actions = {row[0] for row in services.connection.execute("SELECT action FROM audit_events")}
    assert {"user_disabled", "user_enabled", "password_reset", "pending_deleted"} <= actions
    deleted = services.connection.execute(
        "SELECT target_user_id, metadata_json FROM audit_events "
        "WHERE action='pending_deleted'"
    ).fetchone()
    assert deleted["target_user_id"] is None
    assert json.loads(deleted["metadata_json"])["target_username"] == "pending"


def test_admin_requests_running_task_stop(web):
    client, services = web
    user = _create_active(services)
    companion = services.credentials.decrypt_companion(user.id)
    task = services.tasks.create(user.id, TaskDraft(
        "tomorrow", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    services.connection.execute("UPDATE booking_tasks SET status='running' WHERE id=?", (task.id,))
    _admin_login(client, services)
    page = client.get("/admin/tasks")
    response = client.post(
        f"/admin/tasks/{task.id}/stop",
        data={"csrf_token": _csrf(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert services.tasks.get_for_user(user.id, task.id).stop_requested_at == NOW


def test_audit_service_rejects_sensitive_metadata(web):
    _client, services = web
    admin = services.accounts.create_admin("owner", "owner password value", now=NOW)
    with pytest.raises(ValueError, match="敏感"):
        services.audit.record(
            admin.id, "user_disabled", None, "127.0.0.1",
            {"token": "secret"}, now=NOW,
        )


def test_password_reset_endpoint_refuses_admin_target(web):
    client, services = web
    owner = _admin_login(client, services)
    second = services.accounts.create_admin("second", "second password value", now=NOW)
    before = services.connection.execute(
        "SELECT password_hash FROM users WHERE id=?", (second.id,)
    ).fetchone()[0]
    page = client.get("/admin/reauth")
    response = client.post(
        f"/admin/users/{second.id}/reset-password",
        data={"csrf_token": _csrf(page)},
    )
    assert response.status_code == 404
    after = services.connection.execute(
        "SELECT password_hash FROM users WHERE id=?", (second.id,)
    ).fetchone()[0]
    assert after == before
    assert owner.id != second.id


def test_admin_task_detail_shows_sanitized_result_and_log(web):
    client, services = web
    user = _create_active(services)
    companion = services.credentials.decrypt_companion(user.id)
    task = services.tasks.create(user.id, TaskDraft(
        "tomorrow", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    runtime = Path(services.connection.execute("PRAGMA database_list").fetchone()[2]).parent / "runtime" / f"user-{user.id}" / f"task-{task.id}"
    runtime.mkdir(parents=True)
    log = runtime / "worker.log"
    log.write_text("private-token 20260001 safe-line", encoding="utf-8")
    services.connection.execute(
        "INSERT INTO task_runs (task_id,runtime_path,log_path,started_at,finished_at,final_status,detail) "
        "VALUES (?,?,?,?,?,'submission_unknown','manual check')",
        (task.id, str(runtime), str(log), NOW.isoformat(), NOW.isoformat()),
    )
    services.connection.execute(
        "UPDATE task_runs SET detail=? WHERE task_id=?",
        ("manual check private-token 20260001", task.id),
    )
    services.connection.execute(
        "UPDATE booking_tasks SET status='submission_unknown' WHERE id=?", (task.id,)
    )
    _admin_login(client, services)
    response = client.get(f"/admin/tasks/{task.id}")
    assert response.status_code == 200
    assert "manual check" in response.text
    assert "safe-line" in response.text
    assert "private-token" not in response.text
    assert "20260001" not in response.text
