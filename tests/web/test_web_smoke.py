import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jlu_booking.token_validation import TokenValidationResult
from jlu_booking.web.accounts import AccountService
from jlu_booking.web.app import AppServices, create_app
from jlu_booking.web.audit import AuditService, ReauthenticationService
from jlu_booking.web.credentials import CredentialService
from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.scheduler import Scheduler
from jlu_booking.web.security import CredentialCipher, PasswordService, ThrottleService
from jlu_booking.web.sessions import SessionService
from jlu_booking.web.settings import WebSettings
from jlu_booking.web.tasks import ExecutionDateFull, TaskDraft, TaskService
from jlu_booking.web.worker import WorkerResult


BEIJING = ZoneInfo("Asia/Shanghai")
CREATE_TIME = datetime(2026, 9, 22, 6, 30, tzinfo=BEIJING)
START_TIME = datetime(2026, 9, 22, 7, 27, tzinfo=BEIJING)
FINISH_TIME = datetime(2026, 9, 22, 7, 28, tzinfo=BEIJING)


class FakeRunning:
    def __init__(self, task_id):
        self.pid = 9000 + task_id
        self.exit_code = None

    def poll(self):
        return self.exit_code

    def terminate(self, grace_seconds=5.0):
        self.exit_code = -15
        return self.exit_code


class FakeWorker:
    def __init__(self, root):
        self.root = Path(root)
        self.started = {}
        self.paths = []

    def prepare(self, task):
        runtime = self.root / f"user-{task.user_id}" / f"task-{task.id}"
        runtime.mkdir(parents=True)
        config = runtime / "auto_booking.json"
        config.write_text("{}", encoding="utf-8")
        self.paths.append(runtime)
        return SimpleNamespace(
            task_id=task.id,
            runtime_dir=runtime,
            log_path=runtime / "worker.log",
            config_path=config,
        )

    def start(self, launch):
        running = FakeRunning(launch.task_id)
        self.started[launch.task_id] = running
        return running

    def collect_result(self, task, launch, exit_code):
        self.cleanup_snapshot(launch)
        return WorkerResult("success", "fake completed", exit_code)

    def cleanup_snapshot(self, launch):
        launch.config_path.unlink(missing_ok=True)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("smoke tests must not access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", network_forbidden)
    for module in (
        "jlu_booking.web.dependencies",
        "jlu_booking.web.routes.auth",
        "jlu_booking.web.routes.dashboard",
        "jlu_booking.web.routes.task_routes",
    ):
        monkeypatch.setattr(f"{module}.now_beijing", lambda: FINISH_TIME, raising=False)
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
        token_validator=lambda _token: TokenValidationResult("valid", "fake"),
        companion_validator=lambda _number, _token: {"id": 1, "name": "测试同学"},
    )
    tasks = TaskService(connection)
    reauth = ReauthenticationService(connection, passwords, throttles)
    services = AppServices(
        connection, passwords, sessions, throttles, accounts, credentials,
        tasks, AuditService(connection), reauth,
    )
    worker = FakeWorker(settings.runtime_root)
    scheduler = Scheduler(connection, worker)
    app = create_app(settings, services)
    yield SimpleNamespace(
        settings=settings,
        connection=connection,
        services=services,
        worker=worker,
        scheduler=scheduler,
        app=app,
    )
    connection.close()


def _user(harness, index):
    username = f"user{index}"
    user = harness.services.accounts.register_pending(
        username,
        "correct horse battery staple",
        source_ip=f"fake-{index}",
        now=CREATE_TIME,
    )
    harness.services.credentials.activate_user(
        user.id, f"fake-token-{index}", now=CREATE_TIME
    )
    companion = harness.services.credentials.save_companion(
        user.id, "20260001", now=CREATE_TIME
    )
    return harness.services.accounts.get(user.id), companion


def _draft(companion_id):
    return TaskDraft(
        "today", "前卫体育馆", "羽毛球", companion_id, 3,
        [["15:30", "17:30"]], False,
    )


def _csrf(text):
    match = re.search(r'name="csrf_token" value="([^"]+)"', text)
    assert match
    return match.group(1)


def test_public_registration_to_finished_task_without_network(harness):
    user, companion = _user(harness, 1)
    task = harness.services.tasks.create(
        user.id, _draft(companion.id), now=CREATE_TIME
    )
    assert harness.scheduler.run_once(START_TIME).started_task_ids == (task.id,)
    harness.worker.started[task.id].exit_code = 0
    assert harness.scheduler.run_once(FINISH_TIME).finished_task_ids == (task.id,)

    with TestClient(harness.app) as client:
        login = client.get("/login")
        client.post(
            "/login",
            data={
                "username": user.username,
                "password": "correct horse battery staple",
                "csrf_token": _csrf(login.text),
            },
        )
        page = client.get(f"/tasks/{task.id}")
        assert "预约成功" in page.text
        assert "fake-token-1" not in page.text
        assert "20260001" not in page.text


def test_ten_isolated_fake_workers_and_no_eleventh_task(harness):
    tasks = []
    for index in range(10):
        user, companion = _user(harness, index)
        tasks.append(
            harness.services.tasks.create(
                user.id, _draft(companion.id), now=CREATE_TIME
            )
        )
    extra_user, extra_companion = _user(harness, 10)
    with pytest.raises(ExecutionDateFull):
        harness.services.tasks.create(
            extra_user.id, _draft(extra_companion.id), now=CREATE_TIME
        )

    tick = harness.scheduler.run_once(START_TIME)
    assert len(tick.started_task_ids) == 10
    assert len(harness.worker.started) == 10
    assert len(set(harness.worker.paths)) == 10
    assert all("user-" in str(path) and "task-" in str(path) for path in harness.worker.paths)
    assert harness.connection.execute("SELECT COUNT(*) FROM booking_tasks").fetchone()[0] == 10
    for running in harness.worker.started.values():
        running.exit_code = 0
    finished = harness.scheduler.run_once(FINISH_TIME)
    assert len(finished.finished_task_ids) == 10
    assert harness.connection.execute(
        "SELECT COUNT(*) FROM booking_tasks WHERE status='success'"
    ).fetchone()[0] == 10


def test_local_http_shell_and_static_assets(harness):
    with TestClient(harness.app) as client:
        assert client.get("/login").status_code == 200
        assert client.get("/register").status_code == 200
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200
