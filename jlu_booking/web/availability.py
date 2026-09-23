"""Read-only, user-scoped court availability queries for the Web UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..api import extract_available_slots, query_courts, resolve_venue_sport
from ..auto import is_account_blocked_error, is_auth_error, is_rate_limit_error
from .security import require_aware


BEIJING = ZoneInfo("Asia/Shanghai")
ALLOWED_SLOT_KEYS = ("court_name", "court_id", "place_short_name", "start", "end")
QUERY_WINDOW = timedelta(minutes=1)


class AvailabilityQueryError(RuntimeError):
    """A safe, classified school query failure."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class AvailabilityResult:
    venue: str
    sport: str
    query_date: str
    queried_at: datetime
    slots: tuple[dict, ...]


class AvailabilityService:
    def __init__(
        self,
        credentials,
        throttles,
        *,
        query_func=query_courts,
        slots_func=extract_available_slots,
    ):
        self._credentials = credentials
        self._throttles = throttles
        self._query_func = query_func
        self._slots_func = slots_func

    def query(
        self,
        user_id: int,
        venue: str,
        sport: str,
        target_day: str,
        now: datetime,
    ) -> AvailabilityResult:
        local = require_aware(now).astimezone(BEIJING)
        if target_day not in {"today", "tomorrow"}:
            raise ValueError("查询日期只能选择今天或明天。")
        shop_num, short_name = resolve_venue_sport(venue, sport)
        owner = self._credentials._connection.execute(
            "SELECT role, status FROM users WHERE id=?", (int(user_id),)
        ).fetchone()
        if owner is None or owner["role"] != "user" or owner["status"] != "active":
            raise AvailabilityQueryError("access", "当前账号不能查询场地。")
        self._throttles.consume(
            f"manual-query:{int(user_id)}",
            limit=6,
            window=QUERY_WINDOW,
            now=local,
        )
        token = self._credentials.decrypt_token(int(user_id))
        query_date = (
            local.date() + timedelta(days=int(target_day == "tomorrow"))
        ).isoformat()
        try:
            data = self._query_func(
                query_date=query_date,
                sport_short_name=short_name,
                shop_num=shop_num,
                token=token,
            )
            slots = tuple(
                {key: slot[key] for key in ALLOWED_SLOT_KEYS}
                for slot in self._slots_func(data)
            )
        except Exception as exc:
            if is_rate_limit_error(exc):
                raise AvailabilityQueryError(
                    "rate_limit", "学校系统提示请求过于频繁，请稍后再查询。"
                ) from exc
            if is_account_blocked_error(exc):
                raise AvailabilityQueryError(
                    "account_blocked", "学校账号状态异常，请在学校系统核对。"
                ) from exc
            if is_auth_error(exc):
                raise AvailabilityQueryError(
                    "auth", "Token 已失效，请到个人设置重新绑定。"
                ) from exc
            raise AvailabilityQueryError(
                "unavailable", "暂时无法查询学校场地，请稍后重试。"
            ) from exc
        return AvailabilityResult(venue, sport, query_date, local, slots)
