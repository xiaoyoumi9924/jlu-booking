"""Public registration, authentication, and administrator account lifecycle."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import transaction
from .security import PasswordService, ThrottleService, require_aware
from .sessions import SessionService


USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
PENDING_LIFETIME = timedelta(hours=24)
REGISTRATION_WINDOW = timedelta(hours=1)
LOGIN_WINDOW = timedelta(minutes=15)


class AccountError(RuntimeError):
    """Base class for privacy-safe account failures."""


class InvalidUsername(AccountError):
    pass


class UsernameUnavailable(AccountError):
    pass


class PendingUserLimitReached(AccountError):
    pass


class ActiveUserLimitReached(AccountError):
    pass


class AuthenticationFailed(AccountError):
    pass


class UserNotFound(AccountError):
    pass


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    role: str
    status: str
    must_change_password: bool
    created_at: datetime
    activated_at: datetime | None
    pending_expires_at: datetime | None
    disabled_at: datetime | None
    deleted_at: datetime | None


def normalize_username(username: str) -> str:
    value = str(username).strip()
    if not USERNAME_RE.fullmatch(value):
        raise InvalidUsername("用户名必须是 3 至 32 位字母、数字、下划线或连字符。")
    return value.lower()


class AccountService:
    """Own all account-state and password transitions."""

    def __init__(
        self,
        connection,
        passwords: PasswordService,
        sessions: SessionService,
        throttles: ThrottleService,
        *,
        pending_limit: int = 100,
        user_limit: int = 30,
    ):
        self._connection = connection
        self._passwords = passwords
        self._sessions = sessions
        self._throttles = throttles
        self._pending_limit = int(pending_limit)
        self._user_limit = int(user_limit)
        self._dummy_hash = passwords.hash("dummy password verification value")

    @staticmethod
    def _record(row) -> UserRecord:
        def optional_time(name):
            value = row[name]
            return datetime.fromisoformat(value) if value else None

        return UserRecord(
            id=row["id"],
            username=row["username"],
            role=row["role"],
            status=row["status"],
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            activated_at=optional_time("activated_at"),
            pending_expires_at=optional_time("pending_expires_at"),
            disabled_at=optional_time("disabled_at"),
            deleted_at=optional_time("deleted_at"),
        )

    def get(self, user_id: int) -> UserRecord:
        row = self._connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
        if row is None:
            raise UserNotFound("用户不存在。")
        return self._record(row)

    def find_by_username(self, username: str) -> UserRecord | None:
        try:
            normalized = normalize_username(username)
        except InvalidUsername:
            return None
        row = self._connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
        return self._record(row) if row is not None else None

    def register_pending(
        self,
        username: str,
        password: str,
        *,
        source_ip: str,
        now: datetime,
    ) -> UserRecord:
        require_aware(now)
        self._throttles.consume(
            f"register:{source_ip}",
            limit=5,
            window=REGISTRATION_WINDOW,
            now=now,
        )
        normalized = normalize_username(username)
        password_hash = self._passwords.hash(password)
        expires_at = now + PENDING_LIFETIME
        try:
            with transaction(self._connection, immediate=True):
                self._connection.execute(
                    "DELETE FROM users WHERE role = 'user' "
                    "AND status = 'pending_token' "
                    "AND pending_expires_at <= ?",
                    (now.isoformat(),),
                )
                pending_count = self._connection.execute(
                    "SELECT COUNT(*) FROM users "
                    "WHERE role = 'user' AND status = 'pending_token'"
                ).fetchone()[0]
                if pending_count >= self._pending_limit:
                    raise PendingUserLimitReached("待验证注册人数已满，请稍后再试。")
                cursor = self._connection.execute(
                    "INSERT INTO users "
                    "(username, password_hash, role, status, created_at, "
                    "pending_expires_at) VALUES (?, ?, 'user', "
                    "'pending_token', ?, ?)",
                    (
                        normalized,
                        password_hash,
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise UsernameUnavailable("用户名已被使用。") from exc
        return self.get(user_id)

    def cleanup_expired_pending(self, now: datetime) -> int:
        require_aware(now)
        with transaction(self._connection, immediate=True):
            cursor = self._connection.execute(
                "DELETE FROM users WHERE role = 'user' "
                "AND status = 'pending_token' "
                "AND pending_expires_at <= ?",
                (now.isoformat(),),
            )
        return cursor.rowcount

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        source_ip: str,
        now: datetime,
    ) -> UserRecord:
        require_aware(now)
        normalized_for_key = str(username).strip().lower()
        throttle_key = f"login:{normalized_for_key}:{source_ip}"
        self._throttles.require_available(
            throttle_key,
            limit=10,
            window=LOGIN_WINDOW,
            now=now,
        )
        try:
            normalized = normalize_username(username)
        except InvalidUsername:
            normalized = ""
        row = self._connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
        password_hash = row["password_hash"] if row is not None else self._dummy_hash
        password_ok = self._passwords.verify(password_hash, password)
        status_ok = row is not None and row["status"] not in {"disabled", "deleted"}
        if not password_ok or not status_ok:
            self._throttles.consume(
                throttle_key,
                limit=10,
                window=LOGIN_WINDOW,
                now=now,
            )
            raise AuthenticationFailed("用户名或密码错误。")
        self._throttles.clear(throttle_key)
        return self._record(row)

    def create_admin(
        self,
        username: str,
        password: str,
        *,
        now: datetime,
    ) -> UserRecord:
        require_aware(now)
        normalized = normalize_username(username)
        password_hash = self._passwords.hash(password)
        try:
            cursor = self._connection.execute(
                "INSERT INTO users "
                "(username, password_hash, role, status, created_at, activated_at) "
                "VALUES (?, ?, 'admin', 'active', ?, ?)",
                (normalized, password_hash, now.isoformat(), now.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise UsernameUnavailable("用户名已被使用。") from exc
        return self.get(cursor.lastrowid)

    def reset_password(
        self,
        user_id: int,
        temporary_password: str,
        *,
        now: datetime,
    ) -> None:
        require_aware(now)
        password_hash = self._passwords.hash(temporary_password)
        with transaction(self._connection, immediate=True):
            cursor = self._connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1 "
                "WHERE id = ? AND status != 'deleted'",
                (password_hash, int(user_id)),
            )
            if cursor.rowcount != 1:
                raise UserNotFound("用户不存在。")
            self._sessions.invalidate_user_sessions(int(user_id))

    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        *,
        now: datetime,
    ) -> None:
        require_aware(now)
        row = self._connection.execute(
            "SELECT password_hash FROM users WHERE id = ? AND status != 'deleted'",
            (int(user_id),),
        ).fetchone()
        if row is None or not self._passwords.verify(
            row["password_hash"], current_password
        ):
            raise AuthenticationFailed("当前密码错误。")
        new_hash = self._passwords.hash(new_password)
        with transaction(self._connection, immediate=True):
            self._connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 "
                "WHERE id = ?",
                (new_hash, int(user_id)),
            )
            self._sessions.invalidate_user_sessions(int(user_id))

    def disable(self, user_id: int, *, now: datetime) -> UserRecord:
        require_aware(now)
        with transaction(self._connection, immediate=True):
            cursor = self._connection.execute(
                "UPDATE users SET status = 'disabled', disabled_at = ? "
                "WHERE id = ? AND role = 'user' AND status = 'active'",
                (now.isoformat(), int(user_id)),
            )
            if cursor.rowcount != 1:
                raise UserNotFound("可停用用户不存在。")
            self._sessions.invalidate_user_sessions(int(user_id))
        return self.get(user_id)

    def enable(self, user_id: int, *, now: datetime) -> UserRecord:
        require_aware(now)
        with transaction(self._connection, immediate=True):
            row = self._connection.execute(
                "SELECT role, status FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
            if row is None or row["role"] != "user" or row["status"] != "disabled":
                raise UserNotFound("可启用用户不存在。")
            active_count = self._connection.execute(
                "SELECT COUNT(*) FROM users "
                "WHERE role = 'user' AND status = 'active'"
            ).fetchone()[0]
            if active_count >= self._user_limit:
                raise ActiveUserLimitReached("有效用户人数已满。")
            self._connection.execute(
                "UPDATE users SET status = 'active', disabled_at = NULL, "
                "activated_at = COALESCE(activated_at, ?) WHERE id = ?",
                (now.isoformat(), int(user_id)),
            )
        return self.get(user_id)
