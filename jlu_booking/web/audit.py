"""Administrator audit records and session-bound password reauthentication."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta

from .security import RateLimitExceeded, require_aware


ALLOWED_ACTIONS = {
    "user_disabled",
    "user_enabled",
    "pending_deleted",
    "password_reset",
    "task_stop_requested",
    "token_revealed",
    "admin_reauthenticated",
    "admin_reauthentication_failed",
}
ALLOWED_METADATA_KEYS = {"task_id", "status", "outcome", "reason"}
SENSITIVE_MARKERS = {
    "token",
    "password",
    "cookie",
    "csrf",
    "student_number",
}
REAUTH_WINDOW = timedelta(minutes=15)
REAUTH_GRANT = timedelta(minutes=5)


class AuditService:
    def __init__(self, connection):
        self._connection = connection

    def record(
        self,
        admin_id: int,
        action: str,
        target_user_id: int | None,
        source_ip: str,
        metadata: Mapping[str, str],
        *,
        now: datetime,
    ) -> None:
        require_aware(now)
        if action not in ALLOWED_ACTIONS:
            raise ValueError("不允许的审计事件。")
        cleaned = {}
        for key, raw_value in metadata.items():
            normalized_key = str(key).lower()
            value = str(raw_value)
            lowered_value = value.lower()
            if key not in ALLOWED_METADATA_KEYS or any(
                marker in normalized_key or marker in lowered_value
                for marker in SENSITIVE_MARKERS
            ):
                raise ValueError("审计元数据包含敏感或未允许字段。")
            cleaned[str(key)] = value
        self._connection.execute(
            "INSERT INTO audit_events "
            "(admin_id, action, target_user_id, source_ip, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(admin_id),
                action,
                int(target_user_id) if target_user_id is not None else None,
                str(source_ip),
                json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")),
                now.isoformat(),
            ),
        )


class ReauthenticationService:
    def __init__(self, connection, passwords, throttles):
        self._connection = connection
        self._passwords = passwords
        self._throttles = throttles

    def grant(
        self,
        admin_id: int,
        session_id: int,
        password: str,
        *,
        now: datetime,
    ) -> None:
        require_aware(now)
        key = f"reauth:{int(admin_id)}"
        self._throttles.require_available(
            key, limit=5, window=REAUTH_WINDOW, now=now
        )
        row = self._connection.execute(
            "SELECT password_hash FROM users WHERE id = ? AND role = 'admin' "
            "AND status = 'active'",
            (int(admin_id),),
        ).fetchone()
        valid = row is not None and self._passwords.verify(
            row["password_hash"], password
        )
        if not valid:
            self._throttles.consume(
                key, limit=5, window=REAUTH_WINDOW, now=now
            )
            raise PermissionError("管理员密码错误。")
        self._throttles.clear(key)
        changed = self._connection.execute(
            "UPDATE web_sessions SET reauthenticated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (now.isoformat(), int(session_id), int(admin_id)),
        ).rowcount
        if changed != 1:
            raise PermissionError("管理员会话已失效。")

    def is_valid(
        self,
        admin_id: int,
        now: datetime,
        session_id: int | None = None,
    ) -> bool:
        require_aware(now)
        sql = (
            "SELECT reauthenticated_at FROM web_sessions "
            "WHERE user_id = ? AND reauthenticated_at IS NOT NULL"
        )
        parameters: list[object] = [int(admin_id)]
        if session_id is not None:
            sql += " AND id = ?"
            parameters.append(int(session_id))
        sql += " ORDER BY reauthenticated_at DESC LIMIT 1"
        row = self._connection.execute(sql, parameters).fetchone()
        if row is None:
            return False
        granted_at = datetime.fromisoformat(row["reauthenticated_at"])
        return granted_at <= now <= granted_at + REAUTH_GRANT

