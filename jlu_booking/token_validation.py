"""Read-only online validation shared by GUI, CLI, and automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import requests

from .api import (
    DEFAULT_VENUE,
    ServerResponseError,
    get_sports_for_venue,
    query_courts,
    resolve_venue_sport,
)


@dataclass(frozen=True)
class TokenValidationResult:
    """A privacy-safe classification of a read-only Token check."""

    status: str
    reason: str


def _response_text(exc: ServerResponseError) -> str:
    result = exc.result
    if isinstance(result, dict):
        return " ".join(str(value) for value in result.values()).lower()
    return str(result).lower()


def _is_auth_rejection(exc: ServerResponseError) -> bool:
    text = _response_text(exc)
    token_rejected = "token" in text and any(
        marker in text for marker in ("失效", "过期", "无效", "错误")
    )
    return token_rejected or any(
        marker in text
        for marker in (
            "登录已失效",
            "登录过期",
            "请先登录",
            "请重新登录",
            "未登录",
        )
    )


def _is_transport_failure(exc: BaseException) -> bool:
    current = exc
    while current is not None:
        if isinstance(current, requests.RequestException):
            return True
        current = current.__cause__
    text = str(exc)
    return (
        "请求学校服务器失败" in text
        or "服务器返回的数据不是有效 JSON" in text
    )


def validate_token_online(
    token,
    *,
    query_date=None,
    venue_name=DEFAULT_VENUE,
    sport_name=None,
    session=None,
):
    """Check a Token with a query-only endpoint without exposing its value."""

    sports = get_sports_for_venue(venue_name)
    selected_sport = sport_name if sport_name in sports else next(iter(sports))
    shop_num, sport_short_name = resolve_venue_sport(venue_name, selected_sport)

    try:
        query_courts(
            query_date=query_date or date.today().isoformat(),
            sport_short_name=sport_short_name,
            shop_num=shop_num,
            token=token,
            session=session,
        )
    except ServerResponseError as exc:
        if _is_auth_rejection(exc):
            return TokenValidationResult("invalid", "auth_rejected")
        return TokenValidationResult("unavailable", "server_rejected")
    except Exception as exc:
        reason = "transport" if _is_transport_failure(exc) else "error"
        return TokenValidationResult("unavailable", reason)

    return TokenValidationResult("valid", "accepted")
