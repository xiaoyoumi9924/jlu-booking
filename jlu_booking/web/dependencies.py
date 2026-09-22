"""Request helpers shared by the server-rendered Web routes."""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

from .sessions import CsrfError


BEIJING = ZoneInfo("Asia/Shanghai")
SESSION_COOKIE = "jlu_session"
FORM_CSRF_COOKIE = "jlu_form_csrf"


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    settings = request.app.state.settings
    if peer == settings.trusted_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return peer


def anonymous_csrf(request: Request) -> str:
    return request.cookies.get(FORM_CSRF_COOKIE) or secrets.token_urlsafe(32)


def current_session(request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    return request.app.state.services.sessions.resolve(raw, now_beijing())


def current_user(request: Request):
    session = current_session(request)
    if session is None:
        return None, None
    try:
        user = request.app.state.services.accounts.get(session.user_id)
    except Exception:
        return None, None
    if user.status in {"disabled", "deleted"}:
        return None, None
    return session, user


def require_csrf(request: Request, supplied: str, session=None) -> None:
    if session is not None:
        try:
            request.app.state.services.sessions.require_csrf(session, supplied)
        except CsrfError as exc:
            raise HTTPException(status_code=403, detail="请求验证失败。") from exc
        return
    expected = request.cookies.get(FORM_CSRF_COOKIE, "")
    if not expected or not hmac.compare_digest(expected, str(supplied)):
        raise HTTPException(status_code=403, detail="请求验证失败。")
