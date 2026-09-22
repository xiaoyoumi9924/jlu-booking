"""Encrypted per-user JLU credentials and companion validation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..api import get_companion_user
from ..token_store import normalize_token
from ..token_validation import TokenValidationResult, validate_token_online
from .accounts import ActiveUserLimitReached
from .db import transaction
from .security import CredentialCipher, ThrottleService, mask_secret, require_aware


BEIJING = ZoneInfo("Asia/Shanghai")
TOKEN_FAILURE_WINDOW = timedelta(minutes=15)


class CredentialError(RuntimeError):
    """Base class for privacy-safe credential failures."""


class TokenValidationFailed(CredentialError):
    def __init__(self, status: str):
        self.status = str(status)
        messages = {
            "invalid": "Token 无效，请重新获取后再试。",
            "unavailable": "暂时无法验证 Token，请稍后再试。",
            "account_blocked": "账号状态异常，无法完成 Token 验证。",
        }
        super().__init__(messages.get(self.status, "Token 验证未通过。"))


class DuplicateToken(CredentialError):
    pass


class CredentialNotFound(CredentialError):
    pass


class ActivationUnavailable(CredentialError):
    pass


class CompanionValidationFailed(CredentialError):
    pass


class ReauthenticationRequired(CredentialError):
    pass


class CredentialAccessDenied(CredentialError):
    pass


@dataclass(frozen=True)
class CredentialSummary:
    user_id: int
    masked_token: str
    verified_at: datetime
    last_status: str


@dataclass(frozen=True)
class CompanionSummary:
    id: int
    user_id: int
    masked_student_number: str
    name: str
    verified_at: datetime


@dataclass(frozen=True)
class DecryptedCompanion:
    id: int
    user_id: int
    student_number: str
    name: str


class CredentialService:
    """Validate online, then persist only encrypted user-owned values."""

    def __init__(
        self,
        connection,
        cipher: CredentialCipher,
        throttles: ThrottleService,
        *,
        token_validator: Callable[[str], TokenValidationResult] | None = None,
        companion_validator: Callable[[str, str], dict] | None = None,
        reauth_checker: Callable[[int, datetime], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        user_limit: int = 30,
    ):
        self._connection = connection
        self._cipher = cipher
        self._throttles = throttles
        self._clock = clock or (lambda: datetime.now(BEIJING))
        self._token_validator = token_validator or self._validate_token_default
        self._companion_validator = (
            companion_validator or self._validate_companion_default
        )
        self._reauth_checker = reauth_checker or (lambda _admin_id, _now: False)
        self._user_limit = int(user_limit)

    def _validate_token_default(self, token: str) -> TokenValidationResult:
        now = require_aware(self._clock()).astimezone(BEIJING)
        return validate_token_online(token, query_date=now.date().isoformat())

    @staticmethod
    def _validate_companion_default(student_number: str, token: str) -> dict:
        return get_companion_user(student_number=student_number, token=token)

    def _validate_token(self, user_id: int, token: str, now: datetime) -> str:
        require_aware(now)
        normalized = normalize_token(token)
        bucket = f"token:{int(user_id)}"
        self._throttles.require_available(
            bucket,
            limit=5,
            window=TOKEN_FAILURE_WINDOW,
            now=now,
        )
        try:
            result = self._token_validator(normalized)
        except Exception as exc:
            self._throttles.consume(
                bucket,
                limit=5,
                window=TOKEN_FAILURE_WINDOW,
                now=now,
            )
            raise TokenValidationFailed("unavailable") from exc
        if result.status != "valid":
            self._throttles.consume(
                bucket,
                limit=5,
                window=TOKEN_FAILURE_WINDOW,
                now=now,
            )
            raise TokenValidationFailed(result.status)
        self._throttles.clear(bucket)
        return normalized

    def activate_user(
        self,
        user_id: int,
        token: str,
        *,
        now: datetime,
    ) -> CredentialSummary:
        normalized = self._validate_token(user_id, token, now)
        ciphertext = self._cipher.encrypt(normalized)
        blind_index = self._cipher.token_index(normalized)
        try:
            with transaction(self._connection, immediate=True):
                self._connection.execute(
                    "DELETE FROM users WHERE role = 'user' "
                    "AND status = 'pending_token' AND pending_expires_at <= ?",
                    (now.isoformat(),),
                )
                user = self._connection.execute(
                    "SELECT role, status, pending_expires_at FROM users WHERE id = ?",
                    (int(user_id),),
                ).fetchone()
                if (
                    user is None
                    or user["role"] != "user"
                    or user["status"] != "pending_token"
                ):
                    raise ActivationUnavailable("待验证账号不存在或已过期。")
                active_count = self._connection.execute(
                    "SELECT COUNT(*) FROM users "
                    "WHERE role = 'user' AND status = 'active'"
                ).fetchone()[0]
                if active_count >= self._user_limit:
                    raise ActiveUserLimitReached("有效用户人数已满。")
                self._connection.execute(
                    "INSERT INTO user_credentials "
                    "(user_id, token_ciphertext, token_blind_index, verified_at, "
                    "last_status, updated_at) VALUES (?, ?, ?, ?, 'valid', ?)",
                    (
                        int(user_id),
                        ciphertext,
                        blind_index,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._connection.execute(
                    "UPDATE users SET status = 'active', activated_at = ?, "
                    "pending_expires_at = NULL WHERE id = ?",
                    (now.isoformat(), int(user_id)),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateToken("该 Token 已绑定其他账号。") from exc
        return CredentialSummary(
            user_id=int(user_id),
            masked_token=mask_secret(normalized),
            verified_at=now,
            last_status="valid",
        )

    def replace_token(
        self,
        user_id: int,
        token: str,
        *,
        now: datetime,
    ) -> CredentialSummary:
        existing = self._connection.execute(
            "SELECT 1 FROM user_credentials WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if existing is None:
            raise CredentialNotFound("当前账号尚未绑定 Token。")
        normalized = self._validate_token(user_id, token, now)
        ciphertext = self._cipher.encrypt(normalized)
        blind_index = self._cipher.token_index(normalized)
        try:
            with transaction(self._connection, immediate=True):
                cursor = self._connection.execute(
                    "UPDATE user_credentials SET token_ciphertext = ?, "
                    "token_blind_index = ?, verified_at = ?, last_status = 'valid', "
                    "updated_at = ? WHERE user_id = ?",
                    (
                        ciphertext,
                        blind_index,
                        now.isoformat(),
                        now.isoformat(),
                        int(user_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise CredentialNotFound("当前账号尚未绑定 Token。")
        except sqlite3.IntegrityError as exc:
            raise DuplicateToken("该 Token 已绑定其他账号。") from exc
        return CredentialSummary(
            user_id=int(user_id),
            masked_token=mask_secret(normalized),
            verified_at=now,
            last_status="valid",
        )

    def decrypt_token(self, user_id: int) -> str:
        row = self._connection.execute(
            "SELECT token_ciphertext FROM user_credentials WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if row is None:
            raise CredentialNotFound("当前账号尚未绑定 Token。")
        return self._cipher.decrypt(row["token_ciphertext"])

    def save_companion(
        self,
        user_id: int,
        student_number: str,
        *,
        now: datetime,
    ) -> CompanionSummary:
        require_aware(now)
        normalized_number = str(student_number).strip()
        if not normalized_number:
            raise CompanionValidationFailed("同行人学工号不能为空。")
        token = self.decrypt_token(user_id)
        try:
            result = self._companion_validator(normalized_number, token)
            internal_id = result.get("id") if isinstance(result, dict) else None
            name = str(result.get("name", "")).strip() if isinstance(result, dict) else ""
            if internal_id is None or not name:
                raise ValueError("missing companion fields")
        except Exception as exc:
            raise CompanionValidationFailed("同行人验证失败，请检查后重试。") from exc
        number_ciphertext = self._cipher.encrypt(normalized_number)
        name_ciphertext = self._cipher.encrypt(name)
        with transaction(self._connection, immediate=True):
            self._connection.execute(
                "INSERT INTO companions "
                "(user_id, student_number_ciphertext, name_ciphertext, "
                "verified_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "student_number_ciphertext = excluded.student_number_ciphertext, "
                "name_ciphertext = excluded.name_ciphertext, "
                "verified_at = excluded.verified_at, "
                "updated_at = excluded.updated_at",
                (
                    int(user_id),
                    number_ciphertext,
                    name_ciphertext,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        row = self._connection.execute(
            "SELECT id FROM companions WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        return CompanionSummary(
            id=row["id"],
            user_id=int(user_id),
            masked_student_number=mask_secret(normalized_number),
            name=name,
            verified_at=now,
        )

    def decrypt_companion(self, user_id: int) -> DecryptedCompanion:
        row = self._connection.execute(
            "SELECT id, student_number_ciphertext, name_ciphertext "
            "FROM companions WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if row is None:
            raise CredentialNotFound("当前账号尚未保存同行人。")
        return DecryptedCompanion(
            id=row["id"],
            user_id=int(user_id),
            student_number=self._cipher.decrypt(row["student_number_ciphertext"]),
            name=self._cipher.decrypt(row["name_ciphertext"]),
        )

    def reveal_token(
        self,
        admin_id: int,
        target_user_id: int,
        *,
        source_ip: str,
        now: datetime,
    ) -> str:
        require_aware(now)
        admin = self._connection.execute(
            "SELECT role, status FROM users WHERE id = ?",
            (int(admin_id),),
        ).fetchone()
        if admin is None or admin["role"] != "admin" or admin["status"] != "active":
            raise CredentialAccessDenied("没有管理员权限。")
        if not self._reauth_checker(int(admin_id), now):
            raise ReauthenticationRequired("需要重新验证管理员密码。")
        token = self.decrypt_token(target_user_id)
        self._connection.execute(
            "INSERT INTO audit_events "
            "(admin_id, action, target_user_id, source_ip, metadata_json, created_at) "
            "VALUES (?, 'token_revealed', ?, ?, ?, ?)",
            (
                int(admin_id),
                int(target_user_id),
                str(source_ip),
                json.dumps({}, ensure_ascii=False),
                now.isoformat(),
            ),
        )
        return token
