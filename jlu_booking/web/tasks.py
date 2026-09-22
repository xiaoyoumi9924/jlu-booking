"""One-shot booking task validation, ownership, and capacity rules."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..config import validate_auto_config
from .db import transaction
from .security import require_aware


BEIJING = ZoneInfo("Asia/Shanghai")
SCHEDULER_START = time(7, 27)


class TaskError(RuntimeError):
    """Base class for task-domain failures safe to show in the Web UI."""


class UserUnavailable(TaskError):
    pass


class CredentialUnavailable(TaskError):
    pass


class OpenTaskExists(TaskError):
    pass


class ExecutionDateFull(TaskError):
    pass


class TaskNotFound(TaskError):
    pass


class TaskFrozen(TaskError):
    pass


class TaskNotRunning(TaskError):
    pass


@dataclass(frozen=True)
class TaskDraft:
    target_day: str
    venue: str
    sport: str
    companion_id: int
    preferred_court_number: int
    time_priority: list[list[str]] | tuple[tuple[str, str], ...]
    real_booking_enabled: bool = False


@dataclass(frozen=True)
class BookingTask:
    id: int
    user_id: int
    execution_date: date
    target_day: str
    venue: str
    sport: str
    companion_id: int
    preferred_court_number: int
    time_priority: tuple[tuple[str, str], ...]
    real_booking_enabled: bool
    status: str
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None
    stop_requested_at: datetime | None
    cancelled_at: datetime | None


class TaskService:
    """Persist validated tasks while enforcing fixed-date capacity atomically."""

    def __init__(self, connection, *, execution_limit: int = 10):
        self._connection = connection
        self._execution_limit = int(execution_limit)

    @staticmethod
    def next_execution_date(now: datetime) -> date:
        local = require_aware(now).astimezone(BEIJING)
        if local.time() >= SCHEDULER_START:
            return local.date() + timedelta(days=1)
        return local.date()

    @staticmethod
    def target_date(execution_date: date, target_day: str) -> date:
        if target_day == "today":
            return execution_date
        if target_day == "tomorrow":
            return execution_date + timedelta(days=1)
        raise ValueError("target_day 只能是 today 或 tomorrow。")

    @staticmethod
    def _optional_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @classmethod
    def _record(cls, row) -> BookingTask:
        priority = json.loads(row["time_priority_json"])
        return BookingTask(
            id=row["id"],
            user_id=row["user_id"],
            execution_date=date.fromisoformat(row["execution_date"]),
            target_day=row["target_day"],
            venue=row["venue"],
            sport=row["sport"],
            companion_id=row["companion_id"],
            preferred_court_number=row["preferred_court_number"],
            time_priority=tuple(tuple(item) for item in priority),
            real_booking_enabled=bool(row["real_booking_enabled"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            claimed_at=cls._optional_datetime(row["claimed_at"]),
            stop_requested_at=cls._optional_datetime(row["stop_requested_at"]),
            cancelled_at=cls._optional_datetime(row["cancelled_at"]),
        )

    def get_for_user(self, user_id: int, task_id: int) -> BookingTask:
        row = self._connection.execute(
            "SELECT * FROM booking_tasks WHERE id = ? AND user_id = ?",
            (int(task_id), int(user_id)),
        ).fetchone()
        if row is None:
            raise TaskNotFound("预约任务不存在。")
        return self._record(row)

    def _require_eligible_user(self, user_id: int) -> None:
        user = self._connection.execute(
            "SELECT role, status FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
        if user is None or user["role"] != "user" or user["status"] != "active":
            raise UserUnavailable("当前账号不能创建或修改预约任务。")
        credential = self._connection.execute(
            "SELECT last_status FROM user_credentials WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if credential is None or credential["last_status"] != "valid":
            raise CredentialUnavailable("请先绑定并验证有效的 Token。")

    def _normalize_draft(self, user_id: int, draft: TaskDraft) -> dict:
        companion = self._connection.execute(
            "SELECT id FROM companions WHERE id = ? AND user_id = ?",
            (int(draft.companion_id), int(user_id)),
        ).fetchone()
        if companion is None:
            raise ValueError("请选择当前账号已验证的同行人。")
        if draft.target_day not in {"today", "tomorrow"}:
            raise ValueError("target_day 只能是 today 或 tomorrow。")
        normalized = validate_auto_config(
            {
                "target_day": "今天" if draft.target_day == "today" else "明天",
                "venue": draft.venue,
                "sport": draft.sport,
                "companion_student_number": "validated-companion",
                "preferred_court_number": draft.preferred_court_number,
                "time_priority": [list(item) for item in draft.time_priority],
                "real_booking_enabled": draft.real_booking_enabled,
            }
        )
        return {
            "target_day": draft.target_day,
            "venue": normalized["venue"],
            "sport": normalized["sport"],
            "companion_id": int(draft.companion_id),
            "preferred_court_number": normalized["preferred_court_number"],
            "time_priority_json": json.dumps(
                normalized["time_priority"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "real_booking_enabled": int(normalized["real_booking_enabled"]),
        }

    @staticmethod
    def _require_before_cutoff(task: BookingTask, now: datetime) -> None:
        local = require_aware(now).astimezone(BEIJING)
        cutoff = datetime.combine(task.execution_date, SCHEDULER_START, BEIJING)
        if local >= cutoff:
            raise TaskFrozen("07:27 后预约任务已冻结，不能编辑或取消。")

    def create(
        self,
        user_id: int,
        draft: TaskDraft,
        *,
        now: datetime,
    ) -> BookingTask:
        local_now = require_aware(now).astimezone(BEIJING)
        execution_date = self.next_execution_date(local_now)
        try:
            with transaction(self._connection, immediate=True):
                self._require_eligible_user(user_id)
                values = self._normalize_draft(user_id, draft)
                count = self._connection.execute(
                    "SELECT COUNT(*) FROM booking_tasks "
                    "WHERE execution_date = ? AND status != 'cancelled'",
                    (execution_date.isoformat(),),
                ).fetchone()[0]
                if count >= self._execution_limit:
                    raise ExecutionDateFull("该执行日期的 10 个预约名额已满。")
                cursor = self._connection.execute(
                    "INSERT INTO booking_tasks "
                    "(user_id, execution_date, target_day, venue, sport, "
                    "companion_id, preferred_court_number, time_priority_json, "
                    "real_booking_enabled, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)",
                    (
                        int(user_id),
                        execution_date.isoformat(),
                        values["target_day"],
                        values["venue"],
                        values["sport"],
                        values["companion_id"],
                        values["preferred_court_number"],
                        values["time_priority_json"],
                        values["real_booking_enabled"],
                        local_now.isoformat(),
                        local_now.isoformat(),
                    ),
                )
                task_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            if "booking_tasks.user_id" in str(exc):
                raise OpenTaskExists("当前账号已有待执行或运行中的任务。") from exc
            raise
        return self.get_for_user(user_id, task_id)

    def update(
        self,
        user_id: int,
        task_id: int,
        draft: TaskDraft,
        *,
        now: datetime,
    ) -> BookingTask:
        local_now = require_aware(now).astimezone(BEIJING)
        with transaction(self._connection, immediate=True):
            task = self.get_for_user(user_id, task_id)
            if task.status != "scheduled":
                raise TaskFrozen("只有待执行任务可以编辑。")
            self._require_before_cutoff(task, local_now)
            self._require_eligible_user(user_id)
            values = self._normalize_draft(user_id, draft)
            self._connection.execute(
                "UPDATE booking_tasks SET target_day = ?, venue = ?, sport = ?, "
                "companion_id = ?, preferred_court_number = ?, "
                "time_priority_json = ?, real_booking_enabled = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'scheduled'",
                (
                    values["target_day"],
                    values["venue"],
                    values["sport"],
                    values["companion_id"],
                    values["preferred_court_number"],
                    values["time_priority_json"],
                    values["real_booking_enabled"],
                    local_now.isoformat(),
                    int(task_id),
                    int(user_id),
                ),
            )
        return self.get_for_user(user_id, task_id)

    def cancel(
        self,
        user_id: int,
        task_id: int,
        *,
        now: datetime,
    ) -> BookingTask:
        local_now = require_aware(now).astimezone(BEIJING)
        with transaction(self._connection, immediate=True):
            task = self.get_for_user(user_id, task_id)
            if task.status != "scheduled":
                raise TaskFrozen("只有待执行任务可以取消。")
            self._require_before_cutoff(task, local_now)
            self._connection.execute(
                "UPDATE booking_tasks SET status = 'cancelled', updated_at = ?, "
                "cancelled_at = ? WHERE id = ? AND user_id = ? "
                "AND status = 'scheduled'",
                (
                    local_now.isoformat(),
                    local_now.isoformat(),
                    int(task_id),
                    int(user_id),
                ),
            )
        return self.get_for_user(user_id, task_id)

    def request_stop(
        self,
        user_id: int,
        task_id: int,
        *,
        now: datetime,
        on_success=None,
    ) -> BookingTask:
        local_now = require_aware(now).astimezone(BEIJING)
        with transaction(self._connection, immediate=True):
            task = self.get_for_user(user_id, task_id)
            if task.status != "running":
                raise TaskNotRunning("只有运行中的任务可以请求停止。")
            self._connection.execute(
                "UPDATE booking_tasks SET stop_requested_at = "
                "COALESCE(stop_requested_at, ?), updated_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'running'",
                (
                    local_now.isoformat(),
                    local_now.isoformat(),
                    int(task_id),
                    int(user_id),
                ),
            )
            if on_success is not None:
                on_success()
        return self.get_for_user(user_id, task_id)
