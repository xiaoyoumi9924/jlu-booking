"""User-owned recurring booking configuration and task materialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .db import transaction
from .security import require_aware
from .tasks import SCHEDULER_START, TaskDraft, TaskError, TaskService

BEIJING = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailyPlan:
    id: int
    user_id: int
    enabled: bool
    enabled_at: datetime | None
    target_day: str
    venue: str
    sport: str
    companion_id: int
    preferred_court_number: int
    time_priority: tuple[tuple[str, str], ...]
    real_booking_enabled: bool
    created_at: datetime
    updated_at: datetime


class DailyPlanService:
    def __init__(self, connection, *, execution_limit: int = 10):
        self._connection = connection
        self._tasks = TaskService(connection, execution_limit=execution_limit)
        self._execution_limit = int(execution_limit)

    @staticmethod
    def _record(row) -> DailyPlan:
        return DailyPlan(
            id=row["id"], user_id=row["user_id"], enabled=bool(row["enabled"]),
            enabled_at=datetime.fromisoformat(row["enabled_at"]) if row["enabled_at"] else None,
            target_day=row["target_day"], venue=row["venue"], sport=row["sport"],
            companion_id=row["companion_id"], preferred_court_number=row["preferred_court_number"],
            time_priority=tuple(tuple(item) for item in json.loads(row["time_priority_json"])),
            real_booking_enabled=bool(row["real_booking_enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_for_user(self, user_id: int) -> DailyPlan | None:
        row = self._connection.execute(
            "SELECT * FROM daily_booking_plans WHERE user_id=?", (int(user_id),)
        ).fetchone()
        return self._record(row) if row is not None else None

    def save(self, user_id: int, draft: TaskDraft, *, now: datetime) -> DailyPlan:
        local = require_aware(now).astimezone(BEIJING)
        stamp = local.isoformat()
        with transaction(self._connection, immediate=True):
            self._tasks._require_eligible_user(user_id)
            values = self._tasks._normalize_draft(user_id, draft)
            existing = self.get_for_user(user_id)
            if existing is None:
                self._connection.execute(
                    "INSERT INTO daily_booking_plans (user_id,enabled,target_day,venue,sport,"
                    "companion_id,preferred_court_number,time_priority_json,real_booking_enabled,"
                    "created_at,updated_at) VALUES (?,0,?,?,?,?,?,?,?,?,?)",
                    (user_id, values["target_day"], values["venue"], values["sport"],
                     values["companion_id"], values["preferred_court_number"],
                     values["time_priority_json"], values["real_booking_enabled"], stamp, stamp),
                )
            else:
                self._connection.execute(
                    "UPDATE daily_booking_plans SET target_day=?,venue=?,sport=?,companion_id=?,"
                    "preferred_court_number=?,time_priority_json=?,real_booking_enabled=?,updated_at=? "
                    "WHERE id=?",
                    (values["target_day"], values["venue"], values["sport"],
                     values["companion_id"], values["preferred_court_number"],
                     values["time_priority_json"], values["real_booking_enabled"], stamp, existing.id),
                )
                if local.time() < SCHEDULER_START:
                    self._connection.execute(
                        "UPDATE booking_tasks SET target_day=?,venue=?,sport=?,companion_id=?,"
                        "preferred_court_number=?,time_priority_json=?,real_booking_enabled=?,updated_at=? "
                        "WHERE daily_plan_id=? AND execution_date=? AND status='scheduled'",
                        (values["target_day"], values["venue"], values["sport"],
                         values["companion_id"], values["preferred_court_number"],
                         values["time_priority_json"], values["real_booking_enabled"], stamp,
                         existing.id, local.date().isoformat()),
                    )
        return self.get_for_user(user_id)

    def set_enabled(self, user_id: int, enabled: bool, *, now: datetime) -> DailyPlan:
        local = require_aware(now).astimezone(BEIJING)
        stamp = local.isoformat()
        with transaction(self._connection, immediate=True):
            plan = self.get_for_user(user_id)
            if plan is None:
                raise ValueError("请先保存每日预约配置。")
            if enabled:
                self._tasks._require_eligible_user(user_id)
            enabled_at = stamp if enabled and not plan.enabled else plan.enabled_at.isoformat() if plan.enabled_at else None
            self._connection.execute(
                "UPDATE daily_booking_plans SET enabled=?,enabled_at=?,updated_at=? WHERE id=?",
                (int(enabled), enabled_at, stamp, plan.id),
            )
            if not enabled:
                self._connection.execute(
                    "UPDATE booking_tasks SET status='cancelled',cancelled_at=?,updated_at=? "
                    "WHERE daily_plan_id=? AND status='scheduled'",
                    (stamp, stamp, plan.id),
                )
        return self.get_for_user(user_id)

    def materialize(self, now: datetime) -> tuple[int, ...]:
        """Reserve today's remaining automatic slots in enable-time order."""
        local = require_aware(now).astimezone(BEIJING)
        if local.time() >= SCHEDULER_START:
            return ()
        created: list[int] = []
        with transaction(self._connection, immediate=True):
            rows = self._connection.execute(
                "SELECT p.* FROM daily_booking_plans p "
                "JOIN users u ON u.id=p.user_id "
                "JOIN user_credentials c ON c.user_id=p.user_id "
                "JOIN companions companion ON companion.id=p.companion_id AND companion.user_id=p.user_id "
                "WHERE p.enabled=1 AND u.role='user' AND u.status='active' "
                "AND c.last_status='valid' AND p.enabled_at<=? "
                "ORDER BY p.enabled_at,p.user_id",
                (local.isoformat(),),
            ).fetchall()
            count = self._connection.execute(
                "SELECT COUNT(*) FROM booking_tasks WHERE execution_date=? "
                "AND status!='cancelled'", (local.date().isoformat(),)
            ).fetchone()[0]
            for row in rows:
                if count >= self._execution_limit:
                    break
                if self._has_open_or_daily_task(row["user_id"], local.date()):
                    continue
                try:
                    task_id = self._insert_daily_task(row, local)
                except (TaskError, ValueError):
                    continue
                created.append(task_id)
                count += 1
        return tuple(created)

    def _has_open_or_daily_task(self, user_id: int, execution_date: date) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM booking_tasks WHERE user_id=? AND "
            "(status IN ('scheduled','running') OR (execution_date=? AND status!='cancelled')) "
            "LIMIT 1", (user_id, execution_date.isoformat())
        ).fetchone() is not None

    def _insert_daily_task(self, row, now: datetime) -> int:
        draft = TaskDraft(
            target_day=row["target_day"], venue=row["venue"], sport=row["sport"],
            companion_id=row["companion_id"],
            preferred_court_number=row["preferred_court_number"],
            time_priority=json.loads(row["time_priority_json"]),
            real_booking_enabled=bool(row["real_booking_enabled"]),
        )
        values = self._tasks._normalize_draft(row["user_id"], draft)
        cursor = self._connection.execute(
            "INSERT INTO booking_tasks (user_id,execution_date,target_day,venue,sport,companion_id,"
            "preferred_court_number,time_priority_json,real_booking_enabled,status,source,daily_plan_id,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'scheduled','daily',?,?,?)",
            (row["user_id"], now.date().isoformat(), values["target_day"],
             values["venue"], values["sport"], values["companion_id"],
             values["preferred_court_number"], values["time_priority_json"],
             values["real_booking_enabled"], row["id"], now.isoformat(), now.isoformat()),
        )
        return int(cursor.lastrowid)
