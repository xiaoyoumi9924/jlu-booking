"""Short-lived owned choices and durable manual booking attempts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..api import ServerResponseError, book_place, can_book, get_companion_user, resolve_venue_sport
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


@dataclass(frozen=True)
class ManualResult:
    attempt_id: str
    status: str
    kind: str
    detail: str
    venue: str
    sport: str
    query_date: str
    court_name: str
    start: str
    end: str


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

    @staticmethod
    def _require_no_terminal_for_date(db, user_id: int, query_date: str) -> None:
        prior = db.execute(
            "SELECT 1 FROM manual_booking_attempts a "
            "JOIN manual_candidates c ON c.id=a.candidate_id "
            "WHERE a.user_id=? AND c.query_date=? "
            "AND a.status IN ('success','unknown') LIMIT 1",
            (int(user_id), query_date),
        ).fetchone()
        if prior is not None:
            raise ManualBookingError(
                "already_submitted", "目标日期已有手动预约成功或提交结果不明，请先到学校系统核对。"
            )

    def precheck(self, user_id: int, candidate_id: str, now: datetime) -> ManualPrecheck:
        local = self._local(now)
        with closing(connect_database(self._database_path)) as db:
            row = self._candidate_and_owner(db, user_id, candidate_id, local)
            self._require_no_terminal_for_date(db, user_id, row["query_date"])
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
            if not isinstance(check, dict) or check.get("msg") != "success":
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
                self._require_no_terminal_for_date(db, user_id, current["query_date"])
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

    @staticmethod
    def _result(row) -> ManualResult:
        return ManualResult(
            attempt_id=row["id"], status=row["status"], kind=row["kind"],
            detail=row["detail"], venue=row["venue"], sport=row["sport"],
            query_date=row["query_date"], court_name=row["court_name"],
            start=row["start_time"], end=row["end_time"],
        )

    @staticmethod
    def _attempt_row(db, user_id: int, attempt_id: str):
        return db.execute(
            "SELECT a.*,c.venue,c.sport,c.query_date,c.court_name,"
            "c.place_short_name,c.start_time,c.end_time,c.expires_at,"
            "u.role,u.status AS owner_status,cred.last_status,"
            "cred.token_ciphertext,cred.updated_at AS current_credential_updated_at,"
            "companion.id AS current_companion_id,"
            "companion.updated_at AS current_companion_updated_at "
            "FROM manual_booking_attempts a "
            "JOIN manual_candidates c ON c.id=a.candidate_id "
            "JOIN users u ON u.id=a.user_id "
            "LEFT JOIN user_credentials cred ON cred.user_id=a.user_id "
            "LEFT JOIN companions companion ON companion.user_id=a.user_id "
            "WHERE a.id=? AND a.user_id=?",
            (attempt_id, int(user_id)),
        ).fetchone()

    def result_for_user(self, user_id: int, attempt_id: str) -> ManualResult:
        with closing(connect_database(self._database_path)) as db:
            row = self._attempt_row(db, user_id, attempt_id)
            if row is None:
                raise ManualBookingError("not_found", "手动预约记录不存在。")
            return self._result(row)

    def submit(self, user_id: int, attempt_id: str, nonce: str, now: datetime) -> ManualResult:
        local = self._local(now)
        with closing(connect_database(self._database_path)) as db:
            with transaction(db, immediate=True):
                row = self._attempt_row(db, user_id, attempt_id)
                if row is None:
                    raise ManualBookingError("not_found", "手动预约记录不存在。")
                nonce_hash = hashlib.sha256(str(nonce).encode("utf-8")).digest()
                if not hmac.compare_digest(bytes(row["confirmation_hash"]), nonce_hash):
                    raise ManualBookingError("confirmation", "确认凭证无效，请重新查询。")
                if row["role"] != "user" or row["owner_status"] != "active":
                    raise ManualBookingError("access", "当前账号不能提交预约。")
                if row["status"] in {"success", "rejected", "unknown", "submitting"}:
                    return self._result(row)
                self._require_no_terminal_for_date(db, user_id, row["query_date"])
                if row["last_status"] != "valid":
                    raise ManualBookingError("access", "当前账号不能提交预约。")
                if (local >= datetime.fromisoformat(row["expires_at"])
                        or date.fromisoformat(row["query_date"]) < local.date()):
                    raise CandidateUnavailable()
                if (row["current_credential_updated_at"] != row["credential_updated_at"]
                        or row["current_companion_id"] != row["companion_id"]
                        or row["current_companion_updated_at"] != row["companion_updated_at"]):
                    raise ManualBookingError("stale", "Token 或同行人已变化，请重新查询并检查。")
                automatic = db.execute(
                    "SELECT 1 FROM booking_tasks WHERE user_id=? AND "
                    "(status='running' OR (status IN ('success','submission_unknown') "
                    "AND date(execution_date, CASE target_day WHEN 'tomorrow' "
                    "THEN '+1 day' ELSE '+0 day' END)=?)) LIMIT 1",
                    (int(user_id), row["query_date"]),
                ).fetchone()
                if automatic is not None:
                    raise ManualBookingError("busy", "自动预约正在运行或目标日期已有预约结果，请先核对。")
                conflict = db.execute(
                    "SELECT 1 FROM manual_booking_attempts WHERE user_id=? "
                    "AND status='submitting' LIMIT 1", (int(user_id),)
                ).fetchone()
                if conflict is not None:
                    raise ManualBookingError("busy", "已有预约正在提交，请等待结果。")
                token = self._cipher.decrypt(row["token_ciphertext"])
                shop_num, _short_name = resolve_venue_sport(row["venue"], row["sport"])
                changed = db.execute(
                    "UPDATE manual_booking_attempts SET status='submitting',"
                    "updated_at=? WHERE id=? AND status='prechecked'",
                    (local.isoformat(), attempt_id),
                ).rowcount
                if changed != 1:
                    raise ManualBookingError("busy", "预约状态已变化，请查看最新结果。")
                payload = dict(row)
        common = dict(
            query_date=payload["query_date"], start_time=payload["start_time"],
            end_time=payload["end_time"], place_short_name=payload["place_short_name"],
            shop_num=shop_num, token=token,
        )
        try:
            check = self._can_book_func(**common)
            if not isinstance(check, dict) or check.get("msg") != "success":
                raise ManualBookingError("rejected", "学校系统未通过最终预约检查。")
        except ManualBookingError as exc:
            return self._finish(attempt_id, "rejected", exc.kind, str(exc), local)
        except Exception as exc:
            safe = self._safe_error(exc)
            return self._finish(attempt_id, "rejected", safe.kind, str(safe), local)
        school_id = payload["school_companion_id"]
        if str(school_id).isdigit():
            school_id = int(school_id)
        try:
            response = self._book_place_func(
                **common, court_name=payload["court_name"],
                companion_user_ids=[school_id],
            )
            if not isinstance(response, dict):
                raise ValueError("school response was not a mapping")
            if response.get("msg") != "success":
                raise ServerResponseError(response)
        except ServerResponseError as exc:
            safe = self._safe_error(exc)
            return self._finish(attempt_id, "rejected", safe.kind, str(safe), local)
        except Exception as exc:
            if is_rate_limit_error(exc):
                safe = self._safe_error(exc)
                return self._finish(attempt_id, "rejected", safe.kind, str(safe), local)
            return self._finish(
                attempt_id, "unknown", "unknown",
                "提交结果不明，请到学校系统核对；不要重复提交。", local,
            )
        return self._finish(attempt_id, "success", "success", "学校系统已确认预约成功。", local)

    def _finish(
        self, attempt_id: str, status: str, kind: str, detail: str, now: datetime,
    ) -> ManualResult:
        with closing(connect_database(self._database_path)) as db:
            with transaction(db, immediate=True):
                db.execute(
                    "UPDATE manual_booking_attempts SET status=?,kind=?,detail=?,updated_at=? "
                    "WHERE id=? AND status='submitting'",
                    (status, kind, detail, now.isoformat(), attempt_id),
                )
                if status in {"success", "unknown"}:
                    db.execute(
                        "UPDATE booking_tasks SET status='cancelled',cancelled_at=?,updated_at=? "
                        "WHERE status='scheduled' AND user_id=("
                        "SELECT user_id FROM manual_booking_attempts WHERE id=?) "
                        "AND date(execution_date, CASE target_day WHEN 'tomorrow' "
                        "THEN '+1 day' ELSE '+0 day' END)=("
                        "SELECT c.query_date FROM manual_booking_attempts a "
                        "JOIN manual_candidates c ON c.id=a.candidate_id WHERE a.id=?)",
                        (now.isoformat(), now.isoformat(), attempt_id, attempt_id),
                    )
                row = db.execute(
                    "SELECT a.*,c.venue,c.sport,c.query_date,c.court_name,"
                    "c.start_time,c.end_time FROM manual_booking_attempts a "
                    "JOIN manual_candidates c ON c.id=a.candidate_id WHERE a.id=?",
                    (attempt_id,),
                ).fetchone()
                return self._result(row)

    def reconcile_incomplete(self, now: datetime) -> int:
        local = self._local(now)
        with closing(connect_database(self._database_path)) as db:
            with transaction(db, immediate=True):
                changed = db.execute(
                    "UPDATE manual_booking_attempts SET status='unknown',kind='unknown',"
                    "detail='提交结果不明，请到学校系统核对；不要重复提交。',updated_at=? "
                    "WHERE status='submitting'",
                    (local.isoformat(),),
                ).rowcount
                if changed:
                    db.execute(
                        "UPDATE booking_tasks SET status='cancelled',cancelled_at=?,updated_at=? "
                        "WHERE status='scheduled' AND EXISTS ("
                        "SELECT 1 FROM manual_booking_attempts a JOIN manual_candidates c "
                        "ON c.id=a.candidate_id WHERE a.user_id=booking_tasks.user_id "
                        "AND a.status='unknown' AND c.query_date="
                        "date(booking_tasks.execution_date, CASE booking_tasks.target_day "
                        "WHEN 'tomorrow' THEN '+1 day' ELSE '+0 day' END))",
                        (local.isoformat(), local.isoformat()),
                    )
        return changed
