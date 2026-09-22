"""Password, credential, and request-throttle security primitives."""

from __future__ import annotations

import hmac
import math
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from ..token_store import normalize_token
from .db import transaction


def require_aware(value: datetime) -> datetime:
    """Reject ambiguous timestamps at service boundaries."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区信息。")
    return value


class PasswordService:
    """Hash and verify account passwords with Argon2."""

    def __init__(self, hasher: PasswordHasher | None = None):
        self._hasher = hasher or PasswordHasher()

    def hash(self, password: str) -> str:
        candidate = str(password)
        if len(candidate) < 10:
            raise ValueError("密码至少 10 个字符。")
        return self._hasher.hash(candidate)

    def verify(self, password_hash: str, candidate: str) -> bool:
        try:
            return bool(self._hasher.verify(str(password_hash), str(candidate)))
        except (InvalidHashError, VerificationError):
            return False


class CredentialCipher:
    """Encrypt private values and create a keyed Token equality index."""

    def __init__(self, token_key: bytes, blind_key: bytes):
        if len(blind_key) != 32:
            raise ValueError("Token 索引密钥必须是 32 字节。")
        self._fernet = Fernet(token_key)
        self._blind_key = bytes(blind_key)

    def encrypt(self, value: str) -> bytes:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("加密内容不能为空。")
        return self._fernet.encrypt(normalized.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(bytes(ciphertext)).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError) as exc:
            raise ValueError("加密数据无法解密。") from exc

    def token_index(self, token: str) -> bytes:
        normalized = normalize_token(token).encode("utf-8")
        return hmac.digest(self._blind_key, normalized, "sha256")


def mask_secret(value: str) -> str:
    """Return a useful but never complete representation of a private value."""

    text = str(value)
    if not text:
        return ""
    if len(text) <= 2:
        return "*" * len(text)
    if len(text) <= 8:
        return text[0] + ("*" * (len(text) - 2)) + text[-1]
    return text[:4] + ("*" * (len(text) - 8)) + text[-4:]


class RateLimitExceeded(RuntimeError):
    """A security-sensitive action exhausted its current request window."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"请求过于频繁，请在 {self.retry_after_seconds} 秒后重试。")


class ThrottleService:
    """SQLite-backed fixed-window counters shared by Web processes."""

    def __init__(self, connection):
        self._connection = connection

    @staticmethod
    def _validate(limit: int, window: timedelta, now: datetime) -> None:
        require_aware(now)
        if limit <= 0:
            raise ValueError("限流次数必须大于 0。")
        if window.total_seconds() <= 0:
            raise ValueError("限流窗口必须大于 0 秒。")

    @staticmethod
    def _retry_after(window_started_at: datetime, window: timedelta, now: datetime) -> int:
        remaining = (window_started_at + window - now).total_seconds()
        return max(1, math.ceil(remaining))

    def require_available(
        self,
        key: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> None:
        self._validate(limit, window, now)
        with transaction(self._connection, immediate=True):
            row = self._connection.execute(
                "SELECT window_started_at, counter FROM request_throttles "
                "WHERE bucket_key = ?",
                (str(key),),
            ).fetchone()
            if row is None:
                return
            started_at = datetime.fromisoformat(row["window_started_at"])
            if now >= started_at + window:
                self._connection.execute(
                    "DELETE FROM request_throttles WHERE bucket_key = ?",
                    (str(key),),
                )
                return
            if row["counter"] >= limit:
                raise RateLimitExceeded(
                    self._retry_after(started_at, window, now)
                )

    def consume(
        self,
        key: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> None:
        self._validate(limit, window, now)
        bucket_key = str(key)
        with transaction(self._connection, immediate=True):
            row = self._connection.execute(
                "SELECT window_started_at, counter FROM request_throttles "
                "WHERE bucket_key = ?",
                (bucket_key,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO request_throttles "
                    "(bucket_key, window_started_at, counter) VALUES (?, ?, 1)",
                    (bucket_key, now.isoformat()),
                )
                return
            started_at = datetime.fromisoformat(row["window_started_at"])
            if now >= started_at + window:
                self._connection.execute(
                    "UPDATE request_throttles "
                    "SET window_started_at = ?, counter = 1 "
                    "WHERE bucket_key = ?",
                    (now.isoformat(), bucket_key),
                )
                return
            if row["counter"] >= limit:
                raise RateLimitExceeded(
                    self._retry_after(started_at, window, now)
                )
            self._connection.execute(
                "UPDATE request_throttles SET counter = counter + 1 "
                "WHERE bucket_key = ?",
                (bucket_key,),
            )

    def clear(self, key: str) -> None:
        self._connection.execute(
            "DELETE FROM request_throttles WHERE bucket_key = ?",
            (str(key),),
        )
