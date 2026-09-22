"""Separate scheduler that owns booking subprocess lifecycle and recovery."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from ..run_status import RunStatusError, load_run_status
from .db import transaction
from .security import require_aware
from .tasks import BookingTask, TaskService
from .worker import WorkerResult


BEIJING = ZoneInfo("Asia/Shanghai")
START_TIME = time(7, 27)
LAST_START_TIME = time(7, 29, 57)
RECOVERABLE_TERMINAL_STATUSES = {
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
class SchedulerTick:
    started_task_ids: tuple[int, ...] = ()
    finished_task_ids: tuple[int, ...] = ()
    stopped_task_ids: tuple[int, ...] = ()
    missed_task_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ReconciliationResult:
    reconciled_task_ids: tuple[int, ...] = ()


@dataclass
class _OwnedWorker:
    task: BookingTask
    launch: object
    running: object


class Scheduler:
    """Atomically claim tasks, then observe only processes owned by this service."""

    def __init__(
        self,
        connection,
        worker,
        *,
        maintenance=None,
        clock=None,
    ):
        self._connection = connection
        self._worker = worker
        self._maintenance = maintenance
        self._clock = clock or (lambda: datetime.now(BEIJING))
        self._owned: dict[int, _OwnedWorker] = {}
        self._last_maintenance_date = None

    @staticmethod
    def _local(now: datetime) -> datetime:
        return require_aware(now).astimezone(BEIJING)

    def _finalize(
        self,
        task: BookingTask,
        result: WorkerResult,
        now: datetime,
    ) -> None:
        status = result.status if result.status in RECOVERABLE_TERMINAL_STATUSES else "error"
        detail = str(result.detail or "")
        with transaction(self._connection, immediate=True):
            self._connection.execute(
                "UPDATE booking_tasks SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = 'running'",
                (status, now.isoformat(), task.id),
            )
            self._connection.execute(
                "UPDATE task_runs SET finished_at = ?, exit_code = ?, "
                "final_status = ?, detail = ? WHERE task_id = ?",
                (now.isoformat(), result.exit_code, status, detail, task.id),
            )
            if status in {"token_invalid", "account_blocked"}:
                self._connection.execute(
                    "UPDATE user_credentials SET last_status = ?, updated_at = ? "
                    "WHERE user_id = ?",
                    (status, now.isoformat(), task.user_id),
                )

    def _observe_owned(self, now: datetime) -> tuple[list[int], list[int]]:
        finished: list[int] = []
        stopped: list[int] = []
        for task_id, owned in list(self._owned.items()):
            exit_code = owned.running.poll()
            if exit_code is not None:
                result = self._worker.collect_result(
                    owned.task, owned.launch, exit_code
                )
                self._finalize(owned.task, result, now)
                self._owned.pop(task_id, None)
                finished.append(task_id)
                continue
            row = self._connection.execute(
                "SELECT stop_requested_at FROM booking_tasks "
                "WHERE id = ? AND status = 'running'",
                (task_id,),
            ).fetchone()
            if row is None or not row["stop_requested_at"]:
                continue
            exit_code = owned.running.terminate(grace_seconds=5.0)
            self._worker.cleanup_snapshot(owned.launch)
            self._finalize(
                owned.task,
                WorkerResult("stopped", "用户请求停止任务。", exit_code),
                now,
            )
            self._owned.pop(task_id, None)
            stopped.append(task_id)
        return finished, stopped

    def _claim_due(self, now: datetime) -> list[tuple[BookingTask, object]]:
        claimed: list[tuple[BookingTask, object]] = []
        with transaction(self._connection, immediate=True):
            rows = self._connection.execute(
                "SELECT * FROM booking_tasks WHERE execution_date = ? "
                "AND status = 'scheduled' ORDER BY id",
                (now.date().isoformat(),),
            ).fetchall()
            for row in rows:
                task = TaskService._record(row)
                changed = self._connection.execute(
                    "UPDATE booking_tasks SET status = 'running', claimed_at = ?, "
                    "updated_at = ? WHERE id = ? AND status = 'scheduled'",
                    (now.isoformat(), now.isoformat(), task.id),
                ).rowcount
                if changed != 1:
                    continue
                try:
                    launch = self._worker.prepare(task)
                except Exception as exc:
                    self._connection.execute(
                        "UPDATE booking_tasks SET status = 'error', updated_at = ? "
                        "WHERE id = ?",
                        (now.isoformat(), task.id),
                    )
                    self._connection.execute(
                        "INSERT INTO task_runs "
                        "(task_id, runtime_path, log_path, started_at, finished_at, "
                        "final_status, detail) VALUES (?, '', '', ?, ?, 'error', ?)",
                        (
                            task.id,
                            now.isoformat(),
                            now.isoformat(),
                            f"准备工作进程失败：{type(exc).__name__}",
                        ),
                    )
                    continue
                self._connection.execute(
                    "INSERT INTO task_runs "
                    "(task_id, runtime_path, log_path, started_at) VALUES (?, ?, ?, ?)",
                    (
                        task.id,
                        str(launch.runtime_dir),
                        str(launch.log_path),
                        now.isoformat(),
                    ),
                )
                claimed.append((task, launch))
        return claimed

    def _start_claimed(
        self,
        claimed: list[tuple[BookingTask, object]],
        now: datetime,
    ) -> list[int]:
        started: list[int] = []
        for task, launch in claimed:
            try:
                running = self._worker.start(launch)
            except Exception as exc:
                self._worker.cleanup_snapshot(launch)
                self._finalize(
                    task,
                    WorkerResult(
                        "error", f"启动工作进程失败：{type(exc).__name__}", None
                    ),
                    now,
                )
                continue
            self._connection.execute(
                "UPDATE task_runs SET process_id = ? WHERE task_id = ?",
                (running.pid, task.id),
            )
            self._owned[task.id] = _OwnedWorker(task, launch, running)
            started.append(task.id)
        return started

    def _mark_missed(self, now: datetime) -> list[int]:
        missed: list[int] = []
        detail = "预约启动窗口已于 07:29:57 结束，任务未启动。"
        with transaction(self._connection, immediate=True):
            rows = self._connection.execute(
                "SELECT id FROM booking_tasks WHERE execution_date = ? "
                "AND status = 'scheduled' ORDER BY id",
                (now.date().isoformat(),),
            ).fetchall()
            for row in rows:
                task_id = row["id"]
                changed = self._connection.execute(
                    "UPDATE booking_tasks SET status = 'error', updated_at = ? "
                    "WHERE id = ? AND status = 'scheduled'",
                    (now.isoformat(), task_id),
                ).rowcount
                if changed != 1:
                    continue
                self._connection.execute(
                    "INSERT INTO task_runs "
                    "(task_id, runtime_path, log_path, started_at, finished_at, "
                    "final_status, detail) VALUES (?, '', '', ?, ?, 'error', ?)",
                    (task_id, now.isoformat(), now.isoformat(), detail),
                )
                missed.append(task_id)
        return missed

    def run_once(self, now: datetime) -> SchedulerTick:
        local_now = self._local(now)
        finished, stopped = self._observe_owned(local_now)
        started: list[int] = []
        missed: list[int] = []
        if START_TIME <= local_now.time() < LAST_START_TIME:
            started = self._start_claimed(self._claim_due(local_now), local_now)
        elif local_now.time() >= LAST_START_TIME:
            missed = self._mark_missed(local_now)
        return SchedulerTick(
            started_task_ids=tuple(started),
            finished_task_ids=tuple(finished),
            stopped_task_ids=tuple(stopped),
            missed_task_ids=tuple(missed),
        )

    def reconcile(self, now: datetime) -> ReconciliationResult:
        local_now = self._local(now)
        reconciled: list[int] = []
        rows = self._connection.execute(
            "SELECT t.*, r.runtime_path, r.exit_code FROM booking_tasks t "
            "LEFT JOIN task_runs r ON r.task_id = t.id "
            "WHERE t.status = 'running' ORDER BY t.id"
        ).fetchall()
        for row in rows:
            task = TaskService._record(row)
            status = "error"
            if row["runtime_path"]:
                try:
                    payload = load_run_status(
                        Path(row["runtime_path"]) / "state" / "last_run.json"
                    )
                except RunStatusError:
                    payload = None
                if payload and payload.get("status") in RECOVERABLE_TERMINAL_STATUSES:
                    status = str(payload["status"])
            detail = (
                "已从运行状态文件恢复终态。"
                if status != "error"
                else "调度器重启后无法确认完整终态，未重新提交预约。"
            )
            self._finalize(
                task,
                WorkerResult(status, detail, row["exit_code"]),
                local_now,
            )
            reconciled.append(task.id)
        return ReconciliationResult(tuple(reconciled))

    def run_forever(self, *, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        startup = self._local(self._clock())
        self.reconcile(startup)
        if self._maintenance is not None:
            self._maintenance.run(startup)
            self._last_maintenance_date = startup.date()
        try:
            while not stop.is_set():
                now = self._local(self._clock())
                self.run_once(now)
                if (
                    self._maintenance is not None
                    and now.time() >= time(3, 15)
                    and self._last_maintenance_date != now.date()
                ):
                    self._maintenance.run(now)
                    self._last_maintenance_date = now.date()
                stop.wait(1.0)
        finally:
            shutdown_now = self._local(self._clock())
            for task_id, owned in list(self._owned.items()):
                exit_code = owned.running.terminate(grace_seconds=5.0)
                self._worker.cleanup_snapshot(owned.launch)
                self._finalize(
                    owned.task,
                    WorkerResult("stopped", "调度器停止。", exit_code),
                    shutdown_now,
                )
                self._owned.pop(task_id, None)

