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
from jlu_booking.web.tasks import TaskDraft, TaskService


BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 22, 6, 30, tzinfo=BEIJING)


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


@pytest.fixture
def web(tmp_path, monkeypatch):
    for module in (
        "jlu_booking.web.dependencies",
        "jlu_booking.web.routes.auth",
        "jlu_booking.web.routes.profile",
        "jlu_booking.web.routes.dashboard",
        "jlu_booking.web.routes.task_routes",
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
        companion_validator=lambda number, _token: {"id": 1, "name": "同学"},
    )
    services = AppServices(
        connection=connection,
        passwords=passwords,
        sessions=sessions,
        throttles=throttles,
        accounts=accounts,
        credentials=credentials,
        tasks=TaskService(connection),
    )
    with TestClient(create_app(settings, services)) as client:
        yield client, services, settings
    connection.close()


def _create_active_user(services, username, token=None):
    user = services.accounts.register_pending(
        username,
        "long password value",
        source_ip=f"ip-{username}",
        now=NOW,
    )
    services.credentials.activate_user(user.id, token or f"token-{username}", now=NOW)
    services.credentials.save_companion(user.id, "20260001", now=NOW)
    return services.accounts.get(user.id), services.credentials.decrypt_companion(user.id)


def _login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": "long password value",
            "csrf_token": _csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _task_data(csrf, **changes):
    data = {
        "csrf_token": csrf,
        "target_day": "today",
        "venue": "前卫体育馆",
        "sport": "羽毛球",
        "preferred_court_number": "3",
        "priority": ["15:30|17:30", "17:30|19:30"],
        "mode": "scan",
    }
    data.update(changes)
    return data


def test_dashboard_and_create_show_exact_dates(web):
    client, services, _ = web
    user, _ = _create_active_user(services, "alice")
    _login(client, "alice")
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "2026-09-22" in dashboard.text
    form = client.get("/tasks/new")
    assert "2026-09-22" in form.text
    response = client.post(
        "/tasks/new",
        data=_task_data(_csrf(form), target_day="tomorrow"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    task = services.connection.execute(
        "SELECT id, execution_date, target_day FROM booking_tasks WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert tuple(task[1:]) == ("2026-09-22", "tomorrow")
    detail = client.get(response.headers["location"])
    assert "2026-09-23" in detail.text


def test_edit_and_cancel_complete_flow(web):
    client, services, _ = web
    user, companion = _create_active_user(services, "alice", token="private-token")
    task = services.tasks.create(user.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    _login(client, "alice")
    edit = client.get(f"/tasks/{task.id}/edit")
    response = client.post(
        f"/tasks/{task.id}/edit",
        data=_task_data(_csrf(edit), preferred_court_number="5"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert services.tasks.get_for_user(user.id, task.id).preferred_court_number == 5
    detail = client.get(f"/tasks/{task.id}")
    cancelled = client.post(
        f"/tasks/{task.id}/cancel",
        data={"csrf_token": _csrf(detail)},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert services.tasks.get_for_user(user.id, task.id).status == "cancelled"


@pytest.mark.parametrize("suffix", ["", "/edit", "/status"])
def test_user_cannot_read_another_users_task(web, suffix):
    client, services, _ = web
    _create_active_user(services, "alice")
    bob, companion = _create_active_user(services, "bob")
    task = services.tasks.create(bob.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    _login(client, "alice")
    assert client.get(f"/tasks/{task.id}{suffix}").status_code == 404


def test_status_tail_is_owner_scoped_limited_and_redacted(web):
    client, services, settings = web
    user, companion = _create_active_user(services, "alice", token="private-token")
    task = services.tasks.create(user.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    runtime = settings.runtime_root / f"user-{user.id}" / f"task-{task.id}"
    runtime.mkdir(parents=True)
    log = runtime / "worker.log"
    log.write_text(
        "\n".join([f"line-{i}" for i in range(510)] + ["private-token 20260001"]),
        encoding="utf-8",
    )
    services.connection.execute(
        "INSERT INTO task_runs (task_id, runtime_path, log_path, started_at) "
        "VALUES (?, ?, ?, ?)",
        (task.id, str(runtime), str(log), NOW.isoformat()),
    )
    _login(client, "alice")
    response = client.get(f"/tasks/{task.id}/status")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["log_lines"]) == 500
    assert "private-token" not in response.text
    assert "20260001" not in response.text


def test_running_task_stop_sets_database_flag_only(web):
    client, services, _ = web
    user, companion = _create_active_user(services, "alice")
    task = services.tasks.create(user.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    services.connection.execute(
        "UPDATE booking_tasks SET status = 'running' WHERE id = ?", (task.id,)
    )
    _login(client, "alice")
    detail = client.get(f"/tasks/{task.id}")
    response = client.post(
        f"/tasks/{task.id}/stop",
        data={"csrf_token": _csrf(detail)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert services.tasks.get_for_user(user.id, task.id).stop_requested_at == NOW


def test_task_mutation_rate_limit_returns_429_before_service_call(web):
    client, services, _ = web
    user, _ = _create_active_user(services, "alice")
    _login(client, "alice")
    for _ in range(20):
        services.throttles.consume(
            f"task-mutation:{user.id}", limit=20,
            window=__import__("datetime").timedelta(minutes=1), now=NOW,
        )
    form = client.get("/tasks/new")
    response = client.post("/tasks/new", data=_task_data(_csrf(form)))
    assert response.status_code == 429
    assert services.connection.execute("SELECT COUNT(*) FROM booking_tasks").fetchone()[0] == 0


def test_task_is_locked_at_exact_0727(web, monkeypatch):
    client, services, _ = web
    user, companion = _create_active_user(services, "alice")
    task = services.tasks.create(user.id, TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion.id, 3,
        [["15:30", "17:30"]], False,
    ), now=NOW)
    cutoff = datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING)
    monkeypatch.setattr("jlu_booking.web.routes.task_routes.now_beijing", lambda: cutoff)
    _login(client, "alice")
    edit = client.get(f"/tasks/{task.id}/edit")
    assert edit.status_code == 423
