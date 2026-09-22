"""Administrative and service command line entry point for the Web app."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import signal
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from .accounts import AccountError, AccountService
from .db import connect_database, migrate_database
from .security import PasswordService, ThrottleService
from .sessions import SessionService
from .settings import WebSettings


BEIJING = ZoneInfo("Asia/Shanghai")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jlu-booking-web")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate-keys", help="生成 Web 加密密钥")
    generate.add_argument("--directory", required=True, type=Path)

    create_admin = commands.add_parser("create-admin", help="创建管理账号")
    create_admin.add_argument("username")
    commands.add_parser("scheduler", help="运行独立预约调度器")
    commands.add_parser("backup", help="创建 SQLite 日备份")
    serve = commands.add_parser("serve", help="运行本机 Web 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        path.chmod(0o600)


def _generate_keys(directory: Path) -> int:
    target = directory.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        target.chmod(0o700)
    token_path = target / "token.key"
    blind_path = target / "blind.key"
    if token_path.exists() or blind_path.exists():
        raise SystemExit("密钥文件已存在，拒绝覆盖。")

    created = []
    try:
        _write_private_file(token_path, Fernet.generate_key())
        created.append(token_path)
        blind_value = base64.urlsafe_b64encode(secrets.token_bytes(32))
        _write_private_file(blind_path, blind_value)
        created.append(blind_path)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise

    print(f"Token 加密密钥：{token_path}")
    print(f"Token 索引密钥：{blind_path}")
    return 0


def _create_admin(
    username: str,
    *,
    environ: Mapping[str, str],
    password_reader: Callable[[str], str],
) -> int:
    first = password_reader("管理员密码：")
    second = password_reader("再次输入管理员密码：")
    if first != second:
        raise SystemExit("两次输入的密码不一致。")

    settings = WebSettings.from_env(environ)
    connection = connect_database(settings.database_path)
    migrate_database(connection)
    passwords = PasswordService()
    sessions = SessionService(connection)
    accounts = AccountService(
        connection,
        passwords,
        sessions,
        ThrottleService(connection),
        pending_limit=settings.pending_limit,
        user_limit=settings.user_limit,
    )
    try:
        user = accounts.create_admin(
            username,
            first,
            now=datetime.now(BEIJING),
        )
    except AccountError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        connection.close()
    print(f"管理员账号已创建：{user.username}")
    return 0


def _run_backup(*, environ: Mapping[str, str]) -> str:
    from .maintenance import BackupService

    settings = WebSettings.from_env(environ)
    connection = connect_database(settings.database_path)
    try:
        migrate_database(connection)
        path = BackupService(connection, settings.backup_dir).create(
            datetime.now(BEIJING)
        )
    finally:
        connection.close()
    return str(path)


def _run_scheduler(*, environ: Mapping[str, str]) -> int:
    from .credentials import CredentialService
    from .maintenance import MaintenanceService
    from .scheduler import Scheduler
    from .security import CredentialCipher
    from .worker import WorkerAdapter

    settings = WebSettings.from_env(environ)
    connection = connect_database(settings.database_path)
    migrate_database(connection)
    cipher = CredentialCipher(settings.token_key, settings.blind_key)
    credentials = CredentialService(
        connection,
        cipher,
        ThrottleService(connection),
        user_limit=settings.user_limit,
    )
    worker = WorkerAdapter(credentials, settings)
    maintenance = MaintenanceService(connection, settings.runtime_root)
    scheduler = Scheduler(connection, worker, maintenance=maintenance)
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, request_stop)
        scheduler.run_forever(stop_event=stop_event)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        connection.close()
    return 0


def _run_serve(
    *,
    environ: Mapping[str, str],
    host: str,
    port: int,
) -> int:
    import uvicorn

    from .app import create_app

    settings = WebSettings.from_env(environ, strict_permissions=True)
    uvicorn.run(
        create_app(settings),
        host=str(host),
        port=int(port),
        workers=1,
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    password_reader: Callable[[str], str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    reader = getpass.getpass if password_reader is None else password_reader

    if args.command == "generate-keys":
        return _generate_keys(args.directory)
    if args.command == "create-admin":
        return _create_admin(
            args.username,
            environ=environment,
            password_reader=reader,
        )
    if args.command == "scheduler":
        return _run_scheduler(environ=environment)
    if args.command == "backup":
        print(_run_backup(environ=environment))
        return 0
    if args.command == "serve":
        return _run_serve(
            environ=environment,
            host=args.host,
            port=args.port,
        )
    raise SystemExit(f"未知命令：{args.command}")
