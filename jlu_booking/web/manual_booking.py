"""Short-lived owned choices and durable manual booking attempts."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..api import book_place, can_book, get_companion_user, resolve_venue_sport
from ..auto import (
    is_account_blocked_error, is_auth_error, is_daily_booking_limit_error,
    is_rate_limit_error, is_target_unavailable_error,
)
from .availability import AvailabilityResult
from .db import connect_database, transaction
from .security import require_aware

BEIJING = ZoneInfo("Asia/Shanghai")
CANDIDATE_LIFETIME = timedelta(minutes=2)


class ManualBookingError(RuntimeError):
    """A safe error that may be shown in a user's manual booking page."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


class CandidateUnavailable(ManualBookingError):
    def __init__(self):
        super().__init__("candidate_unavailable", "场地选择已过期或不属于当前账号，请重新查询。")


@dataclass(frozen=True)
class ManualPrecheck:
    attempt_id: str
    nonce: str
    venue: str
    sport: str
    query_date: str
    court_name: str
    start: str
    end: str
    companion_name: str


class ManualBookingService:
    def __init__(
        self,
        database_path: Path,
        credentials,
        *,
        can_book_func=can_book,
        book_place_func=book_place,
        companion_func=get_companion_user,
    ):
        self._database_path = Path(database_path)
        self._cipher = credentials._cipher
        self._can_book_func = can_book_func
        self._book_place_func = book_place_func
        self._companion_func = companion_func

    @staticmethod
    def _local(now: datetime) -> datetime:
        return require_aware(now).astimezone(BEIJING)

    @staticmethod
    def _safe_error(exc: Exception) -> ManualBookingError:
        if is_rate_limit_error(exc):
            return ManualBookingError("rate_limit", "学校系统提示请求过于频繁，请稍后再试。")
        if is_account_blocked_error(exc):
            return ManualBookingError("account_blocked", "学校账号状态异常，请到学校系统核对。")
        if is_auth_error(exc):
            return ManualBookingError("auth", "Token 已失效，请重新绑定。")
        if is_daily_booking_limit_error(exc):
            return ManualBookingError("daily_limit", "学校系统提示当天预约次数已达上限。")
        if is_target_unavailable_error(exc):
            return ManualBookingError("target_unavailable", "该场地时段已不可预约，请重新查询。")
        return ManualBookingError("rejected", "学校系统未通过预约检查，请重新查询。")

    def register_candidates(self, user_id: int, result: AvailabilityResult) -> list[dict]:
        queried_at = self._local(result.queried_at)
        try:
            target = date.fromisoformat(result.query_date)
            _shop_num, sport_short_name = resolve_venue_sport(result.venue, result.sport)
        except ValueError:
            return []
        if target not in {queried_at.date(), queried_at.date() + timedelta(days=1)}:
            return []
        saved: list[dict] = []
        with closing(connect_database(self._database_path)) as db:
            with transaction(db, immediate=True):
                owner = db.execute(
                    "SELECT u.role,u.status,c.last_status FROM users u "
                    "LEFT JOIN user_credentials c ON c.user_id=u.id WHERE u.id=?",
                    (int(user_id),),
                ).fetchone()
                if owner is None or tuple(owner) != ("user", "active", "valid"):
                    raise CandidateUnavailable()
                for slot in result.slots:
                    court_name = str(slot.get("court_name", "")).strip()
                    short_name = str(slot.get("place_short_name", "")).strip()
                    start = str(slot.get("start", "")).strip()
                    end = str(slot.get("end", "")).strip()
                    if (not court_name or not short_name.startswith(sport_short_name)
                            or len(short_name) <= len(sport_short_name)
                            or not self._valid_time(start, end)):
                        continue
                    candidate_id = secrets.token_urlsafe(24)
                    db.execute(
                        "INSERT INTO manual_candidates "
                        "(id,user_id,venue,sport,query_date,court_name,place_short_name,"
                        "start_time,end_time,created_at,expires_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (candidate_id, user_id, result.venue, result.sport,
                         result.query_date, court_name, short_name, start, end,
                         queried_at.isoformat(), (queried_at + CANDIDATE_LIFETIME).isoformat()),
                    )
                    saved.append({
                        "candidate_id": candidate_id, "court_name": court_name,
                        "court_id": slot.get("court_id", ""),
                        "place_short_name": short_name, "start": start, "end": end,
                    })
        return saved

    @staticmethod
    def _valid_time(start: str, end: str) -> bool:
        try:
            return datetime.strptime(start, "%H:%M").time() < datetime.strptime(end, "%H:%M").time()
        except ValueError:
            return False

    def _candidate_and_owner(self, db, user_id: int, candidate_id: str, now: datetime):
        row = db.execute(
            "SELECT candidate.*,u.role,u.status AS owner_status,"
            "credential.token_ciphertext,credential.last_status,"
            "credential.updated_at AS credential_updated_at,"
            "companion.id AS companion_id,companion.student_number_ciphertext,"
            "companion.name_ciphertext,companion.updated_at AS companion_updated_at "
            "FROM manual_candidates candidate JOIN users u ON u.id=candidate.user_id "
            "LEFT JOIN user_credentials credential ON credential.user_id=u.id "
            "LEFT JOIN companions companion ON companion.user_id=u.id "
            "WHERE candidate.id=? AND candidate.user_id=?",
            (candidate_id, int(user_id)),
        ).fetchone()
        if (row is None or row["role"] != "user" or row["owner_status"] != "active"
                or row["last_status"] != "valid" or row["companion_id"] is None
                or now >= datetime.fromisoformat(row["expires_at"])
                or date.fromisoformat(row["query_date"]) < now.date()):
            raise CandidateUnavailable()
        return row

    def precheck(self, user_id: int, candidate_id: str, now: datetime) -> ManualPrecheck:
        local = self._local(now)
        with closing(connect_database(self._database_path)) as db:
            row = self._candidate_and_owner(db, user_id, candidate_id, local)
            candidate = dict(row)
            token = self._cipher.decrypt(row["token_ciphertext"])
            companion_number = self._cipher.decrypt(row["student_number_ciphertext"])
            companion_name = self._cipher.decrypt(row["name_ciphertext"])
        shop_num, _short_name = resolve_venue_sport(candidate["venue"], candidate["sport"])
        try:
            check = self._can_book_func(
                query_date=candidate["query_date"], start_time=candidate["start_time"],
                end_time=candidate["end_time"], place_short_name=candidate["place_short_name"],
                shop_num=shop_num, token=token,
            )
            if isinstance(check, dict) and check.get("msg") != "success":
                raise ManualBookingError("rejected", "学校系统未通过预约检查，请重新查询。")
            companion = self._companion_func(student_number=companion_number, token=token)
            school_companion_id = companion.get("id")
            if school_companion_id is None:
                raise ManualBookingError("companion", "学校系统无法验证同行人，请到个人设置重新核对。")
        except ManualBookingError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from exc
        nonce = secrets.token_urlsafe(24)
        attempt_id = secrets.token_urlsafe(24)
        nonce_hash = hashlib.sha256(nonce.encode("ascii")).digest()
        with closing(connect_database(self._database_path)) as db:
            with transaction(db, immediate=True):
                current = self._candidate_and_owner(db, user_id, candidate_id, local)
                if (current["credential_updated_at"] != candidate["credential_updated_at"]
                        or current["companion_updated_at"] != candidate["companion_updated_at"]):
                    raise CandidateUnavailable()
                db.execute(
                    "INSERT INTO manual_booking_attempts "
                    "(id,user_id,candidate_id,companion_id,companion_updated_at,"
                    "school_companion_id,credential_updated_at,confirmation_hash,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,'prechecked',?,?)",
                    (attempt_id, user_id, candidate_id, candidate["companion_id"],
                     candidate["companion_updated_at"], str(school_companion_id),
                     candidate["credential_updated_at"], nonce_hash,
                     local.isoformat(), local.isoformat()),
                )
        return ManualPrecheck(
            attempt_id, nonce, candidate["venue"], candidate["sport"],
            candidate["query_date"], candidate["court_name"],
            candidate["start_time"], candidate["end_time"], companion_name,
        )
