"""Retention cleanup and safe daily SQLite backups for the Web service."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .db import transaction
from .security import require_aware


SESSION_IDLE = timedelta(hours=12)
THROTTLE_MAX_AGE = timedelta(days=1)
LOG_RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class MaintenanceResult:
    expired_sessions: int
    expired_throttles: int
    expired_pending_users: int
    deleted_logs: int
    deleted_runtime_directories: int = 0


@dataclass(frozen=True)
class BackupResult:
    path: Path


class MaintenanceService:
    def __init__(self, connection, runtime_root: Path | str):
        self._connection = connection
        self._runtime_root = Path(runtime_root)

    def run(self, now: datetime) -> MaintenanceResult:
        require_aware(now)
        with transaction(self._connection, immediate=True):
            sessions = self._connection.execute(
                "DELETE FROM web_sessions WHERE expires_at <= ? OR last_seen_at <= ?",
                (now.isoformat(), (now - SESSION_IDLE).isoformat()),
            ).rowcount
            throttles = self._connection.execute(
                "DELETE FROM request_throttles WHERE window_started_at <= ?",
                ((now - THROTTLE_MAX_AGE).isoformat(),),
            ).rowcount
            pending = self._connection.execute(
                "DELETE FROM users WHERE role = 'user' "
                "AND status = 'pending_token' AND pending_expires_at <= ?",
                (now.isoformat(),),
            ).rowcount
        deleted_logs = self._delete_old_logs(now)
        deleted_runtime_directories = self._delete_old_runtime_directories(now)
        return MaintenanceResult(
            expired_sessions=sessions,
            expired_throttles=throttles,
            expired_pending_users=pending,
            deleted_logs=deleted_logs,
            deleted_runtime_directories=deleted_runtime_directories,
        )

    def _delete_old_runtime_directories(self, now: datetime) -> int:
        root = self._runtime_root
        if root.is_symlink() or not root.exists():
            return 0
        resolved_root = root.resolve(strict=False)
        cutoff = (now - LOG_RETENTION).timestamp()
        rows = self._connection.execute(
            "SELECT r.runtime_path FROM task_runs r JOIN booking_tasks t ON t.id=r.task_id "
            "WHERE t.status NOT IN ('scheduled','running') AND r.runtime_path != ''"
        ).fetchall()
        removed = 0
        for row in rows:
            candidate = Path(row["runtime_path"])
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            try:
                candidate.resolve(strict=False).relative_to(resolved_root)
            except ValueError:
                continue
            try:
                if candidate.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            for current, directories, filenames in os.walk(
                candidate, topdown=False, followlinks=False
            ):
                current_path = Path(current)
                for filename in filenames:
                    (current_path / filename).unlink(missing_ok=True)
                for directory in directories:
                    child = current_path / directory
                    if child.is_symlink():
                        child.unlink(missing_ok=True)
                    else:
                        child.rmdir()
            candidate.rmdir()
            removed += 1
        return removed

    def _delete_old_logs(self, now: datetime) -> int:
        root = self._runtime_root
        if root.is_symlink():
            raise ValueError(f"运行目录不能是符号链接：{root}")
        if not root.exists():
            return 0
        cutoff = (now - LOG_RETENTION).timestamp()
        removed = 0
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name for name in directories if not (current_path / name).is_symlink()
            ]
            if current_path.name != "logs":
                continue
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink() or not (
                    filename.endswith(".log") or ".log." in filename
                ):
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
        return removed


class BackupService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        backup_dir: Path | str,
        *,
        retention: int = 14,
    ):
        self._connection = connection
        self._backup_dir = Path(backup_dir)
        self._retention = int(retention)

    def create(self, now: datetime) -> Path:
        require_aware(now)
        target_dir = self._backup_dir
        if target_dir.is_symlink():
            raise ValueError(f"备份目录不能是符号链接：{target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            target_dir.chmod(0o700)
        target = target_dir / f"{now.date().isoformat()}.sqlite3"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            destination = sqlite3.connect(temporary)
            try:
                self._connection.backup(destination)
            finally:
                destination.close()
            if os.name != "nt":
                temporary.chmod(0o600)
            with temporary.open("rb") as backup_file:
                os.fsync(backup_file.fileno())
            os.replace(temporary, target)
            if os.name != "nt":
                target.chmod(0o600)
            self._remove_old_backups()
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def _remove_old_backups(self) -> None:
        backups = sorted(self._backup_dir.glob("????-??-??.sqlite3"))
        for path in backups[: max(0, len(backups) - self._retention)]:
            if not path.is_symlink():
                path.unlink(missing_ok=True)
