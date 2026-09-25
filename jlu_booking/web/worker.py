"""Isolated adapter that launches the existing automatic-booking worker."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..app_runner import build_auto_worker_command
from ..config import save_auto_config, validate_auto_config
from ..run_status import RunStatusError, load_run_status
from .tasks import BookingTask


LOG_LIMIT_BYTES = 10 * 1024 * 1024
TERMINAL_STATUSES = {
    "success",
    "no_result",
    "token_invalid",
    "account_blocked",
    "daily_limit",
    "submission_unknown",
    "network_unavailable",
    "stopped",
    "error",
}


@dataclass(frozen=True)
class WorkerLaunch:
    task_id: int
    command: tuple[str, ...]
    environment: Mapping[str, str] = field(repr=False)
    config_path: Path
    runtime_dir: Path
    log_path: Path
    redactions: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class WorkerResult:
    status: str
    detail: str
    exit_code: int | None


class RunningWorker:
    """Small process handle that also owns the continuous log-pump thread."""

    def __init__(self, process, log_pump: threading.Thread):
        self._process = process
        self._log_pump = log_pump

    @property
    def pid(self) -> int:
        return int(self._process.pid)

    def poll(self) -> int | None:
        return self._process.poll()

    def join_log_pump(self, timeout: float | None = None) -> None:
        self._log_pump.join(timeout)

    def terminate(self, grace_seconds: float = 5.0) -> int:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                exit_code = self._process.wait(timeout=float(grace_seconds))
            except subprocess.TimeoutExpired:
                self._process.kill()
                exit_code = self._process.wait()
        else:
            exit_code = self._process.wait()
        self.join_log_pump()
        return int(exit_code)


class WorkerAdapter:
    """Prepare private snapshots and directly launch the unchanged core."""

    def __init__(
        self,
        credentials,
        settings,
        *,
        python_executable: str | None = None,
        base_environment: Mapping[str, str] | None = None,
    ):
        self._credentials = credentials
        self._runtime_root = Path(settings.runtime_root)
        self._python_executable = python_executable or sys.executable
        self._base_environment = dict(
            os.environ if base_environment is None else base_environment
        )
        self._redactions: dict[int, tuple[str, ...]] = {}
        self._running: dict[int, RunningWorker] = {}

    @staticmethod
    def _private_directory(path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"运行目录不能是符号链接：{path}")
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError(f"运行目录不能是符号链接：{path}")
        if os.name != "nt":
            path.chmod(0o700)

    def prepare(self, task: BookingTask) -> WorkerLaunch:
        token = self._credentials.decrypt_token(task.user_id)
        companion = self._credentials.decrypt_companion(task.user_id)
        if companion.id != task.companion_id or companion.user_id != task.user_id:
            raise ValueError("任务同行人与当前账号不匹配。")

        self._private_directory(self._runtime_root)
        user_dir = self._runtime_root / f"user-{task.user_id}"
        self._private_directory(user_dir)
        runtime_dir = user_dir / f"task-{task.id}"
        self._private_directory(runtime_dir)
        log_dir = runtime_dir / "logs"
        self._private_directory(log_dir)

        config_path = runtime_dir / "auto_booking.json"
        config = validate_auto_config(
            {
                "target_day": "今天" if task.target_day == "today" else "明天",
                "venue": task.venue,
                "sport": task.sport,
                "companion_student_number": companion.student_number,
                "preferred_court_number": task.preferred_court_number,
                "time_priority": [list(item) for item in task.time_priority],
                "real_booking_enabled": task.real_booking_enabled,
            }
        )
        save_auto_config(config, config_path)
        if os.name != "nt":
            config_path.chmod(0o600)

        command = tuple(
            build_auto_worker_command(
                real_booking_enabled=task.real_booking_enabled,
                executable=self._python_executable,
                frozen=False,
            )
        )
        if task.start_mode == "immediate":
            command += ("--immediate",)
        environment = dict(self._base_environment)
        environment.update(
            {
                "JLU_BOOKING_TOKEN": token,
                "JLU_BOOKING_CONFIG_FILE": str(config_path),
                "JLU_BOOKING_RUNTIME_DIR": str(runtime_dir),
            }
        )
        redactions = tuple(value for value in (token, companion.student_number) if value)
        self._redactions[task.id] = redactions
        return WorkerLaunch(
            task_id=task.id,
            command=command,
            environment=environment,
            config_path=config_path,
            runtime_dir=runtime_dir,
            log_path=log_dir / "worker.log",
            redactions=redactions,
        )

    @staticmethod
    def _sanitize(redactions: tuple[str, ...], line: str) -> str:
        sanitized = str(line)
        for private_value in redactions:
            sanitized = sanitized.replace(private_value, "[REDACTED]")
        return sanitized

    def sanitize_line(self, task: BookingTask, line: str) -> str:
        redactions = self._redactions.get(task.id)
        if redactions is None:
            token = self._credentials.decrypt_token(task.user_id)
            companion = self._credentials.decrypt_companion(task.user_id)
            redactions = (token, companion.student_number)
            self._redactions[task.id] = redactions
        return self._sanitize(redactions, line)

    @staticmethod
    def _rotate(log_path: Path) -> None:
        oldest = log_path.with_name(f"{log_path.name}.3")
        oldest.unlink(missing_ok=True)
        for number in (2, 1):
            source = log_path.with_name(f"{log_path.name}.{number}")
            if source.exists():
                source.replace(log_path.with_name(f"{log_path.name}.{number + 1}"))
        if log_path.exists():
            log_path.replace(log_path.with_name(f"{log_path.name}.1"))

    @classmethod
    def _pump_output(
        cls,
        stream,
        log_path: Path,
        redactions: tuple[str, ...],
    ) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        for line in stream:
            sanitized = cls._sanitize(redactions, line)
            encoded_size = len(sanitized.encode("utf-8"))
            current_size = log_path.stat().st_size if log_path.exists() else 0
            if current_size and current_size + encoded_size > LOG_LIMIT_BYTES:
                cls._rotate(log_path)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(sanitized)
                log_file.flush()
        try:
            stream.close()
        except (AttributeError, OSError):
            pass

    def start(self, launch: WorkerLaunch) -> RunningWorker:
        process = subprocess.Popen(
            list(launch.command),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(launch.environment),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process.stdout is None:  # defensive: PIPE above must provide it
            process.kill()
            raise RuntimeError("无法读取预约子进程输出。")
        pump = threading.Thread(
            target=self._pump_output,
            args=(process.stdout, launch.log_path, launch.redactions),
            name=f"booking-log-{launch.task_id}",
            daemon=True,
        )
        pump.start()
        running = RunningWorker(process, pump)
        self._running[launch.task_id] = running
        return running

    def collect_result(
        self,
        task: BookingTask,
        launch: WorkerLaunch,
        exit_code: int | None,
    ) -> WorkerResult:
        running = self._running.pop(task.id, None)
        if running is not None:
            running.join_log_pump()
        try:
            status_path = launch.runtime_dir / "state" / "last_run.json"
            try:
                payload = load_run_status(status_path)
            except RunStatusError:
                payload = None
            if payload is not None and payload.get("status") in TERMINAL_STATUSES:
                status = str(payload["status"])
                detail = str(payload.get("detail", ""))
            else:
                status = "error"
                detail = f"未获得完整运行状态（exit_code={exit_code}）。"
            detail = self._sanitize(launch.redactions, detail)
            return WorkerResult(status=status, detail=detail, exit_code=exit_code)
        finally:
            self.cleanup_snapshot(launch)
            self._redactions.pop(task.id, None)

    @staticmethod
    def cleanup_snapshot(launch: WorkerLaunch) -> None:
        launch.config_path.unlink(missing_ok=True)

    def cleanup_recovered_snapshot(self, runtime_dir: Path | str) -> None:
        root = self._runtime_root
        candidate = Path(runtime_dir)
        if root.is_symlink() or candidate.is_symlink():
            raise ValueError("恢复目录不能是符号链接。")
        resolved_root = root.resolve(strict=False)
        try:
            relative = candidate.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("恢复目录超出 Web 运行根目录。") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("恢复目录不能包含符号链接。")
        snapshot = candidate / "auto_booking.json"
        if snapshot.is_symlink():
            raise ValueError("配置快照不能是符号链接。")
        snapshot.unlink(missing_ok=True)
