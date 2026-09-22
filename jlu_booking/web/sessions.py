"""Opaque server-side login sessions and CSRF validation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import transaction
from .security import require_aware


IDLE_TIMEOUT = timedelta(hours=12)
ABSOLUTE_TIMEOUT = timedelta(days=7)


class CsrfError(RuntimeError):
    """A state-changing request did not prove same-session intent."""


@dataclass(frozen=True)
class SessionGrant:
    session_id: int
    raw_token: str
    csrf_token: str


@dataclass(frozen=True)
class SessionRecord:
    id: int
    user_id: int
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    reauthenticated_at: datetime | None


class SessionService:
    """Create, resolve, and revoke hashed opaque browser sessions."""

    def __init__(self, connection):
        self._connection = connection

    @staticmethod
    def _hash(raw_token: str) -> bytes:
        return hashlib.sha256(str(raw_token).encode("utf-8")).digest()

    @staticmethod
    def _record(row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            csrf_token=row["csrf_secret"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            reauthenticated_at=(
                datetime.fromisoformat(row["reauthenticated_at"])
                if row["reauthenticated_at"]
                else None
            ),
        )

    def create(self, user_id: int, now: datetime) -> SessionGrant:
        require_aware(now)
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        cursor = self._connection.execute(
            "INSERT INTO web_sessions "
            "(token_hash, user_id, csrf_secret, created_at, last_seen_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self._hash(raw_token),
                int(user_id),
                csrf_token,
                now.isoformat(),
                now.isoformat(),
                (now + ABSOLUTE_TIMEOUT).isoformat(),
            ),
        )
        return SessionGrant(
            session_id=cursor.lastrowid,
            raw_token=raw_token,
            csrf_token=csrf_token,
        )

    def resolve(self, raw_token: str, now: datetime) -> SessionRecord | None:
        require_aware(now)
        token_hash = self._hash(raw_token)
        with transaction(self._connection, immediate=True):
            row = self._connection.execute(
                "SELECT * FROM web_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            record = self._record(row)
            if (
                now >= record.expires_at
                or now >= record.last_seen_at + IDLE_TIMEOUT
            ):
                self._connection.execute(
                    "DELETE FROM web_sessions WHERE id = ?",
                    (record.id,),
                )
                return None
            self._connection.execute(
                "UPDATE web_sessions SET last_seen_at = ? WHERE id = ?",
                (now.isoformat(), record.id),
            )
            return SessionRecord(
                id=record.id,
                user_id=record.user_id,
                csrf_token=record.csrf_token,
                created_at=record.created_at,
                last_seen_at=now,
                expires_at=record.expires_at,
                reauthenticated_at=record.reauthenticated_at,
            )

    def require_csrf(self, session: SessionRecord, supplied: str) -> None:
        if not hmac.compare_digest(session.csrf_token, str(supplied)):
            raise CsrfError("请求验证失败，请刷新页面后重试。")

    def invalidate_user_sessions(
        self,
        user_id: int,
        except_session_id: int | None = None,
    ) -> None:
        if except_session_id is None:
            self._connection.execute(
                "DELETE FROM web_sessions WHERE user_id = ?",
                (int(user_id),),
            )
            return
        self._connection.execute(
            "DELETE FROM web_sessions WHERE user_id = ? AND id != ?",
            (int(user_id), int(except_session_id)),
        )

    def invalidate(self, raw_token: str) -> None:
        self._connection.execute(
            "DELETE FROM web_sessions WHERE token_hash = ?",
            (self._hash(raw_token),),
        )
