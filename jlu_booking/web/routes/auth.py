"""Registration, login, logout, and password-change HTML routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..accounts import AccountError, AuthenticationFailed
from ..dependencies import (
    FORM_CSRF_COOKIE,
    SESSION_COOKIE,
    anonymous_csrf,
    client_ip,
    current_user,
    now_beijing,
    require_csrf,
)


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _set_form_csrf(request: Request, response, token: str) -> None:
    response.set_cookie(
        FORM_CSRF_COOKIE,
        token,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
        max_age=3600,
    )


def _auth_destination(user) -> str:
    if user.must_change_password:
        return "/change-password"
    if user.status == "pending_token":
        return "/onboarding/token"
    return "/profile"


@router.get("/", include_in_schema=False)
async def index(request: Request):
    _session, user = current_user(request)
    return RedirectResponse(_auth_destination(user) if user else "/login", 303)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    token = anonymous_csrf(request)
    response = _templates(request).TemplateResponse(
        request=request,
        name="register.html",
        context={"csrf_token": token, "error": None},
    )
    _set_form_csrf(request, response, token)
    return response


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    require_csrf(request, csrf_token)
    try:
        request.app.state.services.accounts.register_pending(
            username,
            password,
            source_ip=client_ip(request),
            now=now_beijing(),
        )
    except (AccountError, ValueError) as exc:
        token = anonymous_csrf(request)
        response = _templates(request).TemplateResponse(
            request=request,
            name="register.html",
            context={"csrf_token": token, "error": str(exc)},
            status_code=400,
        )
        _set_form_csrf(request, response, token)
        return response
    return RedirectResponse("/login", 303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    _session, user = current_user(request)
    if user is not None:
        return RedirectResponse(_auth_destination(user), 303)
    token = anonymous_csrf(request)
    response = _templates(request).TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": token, "error": None},
    )
    _set_form_csrf(request, response, token)
    return response


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    require_csrf(request, csrf_token)
    try:
        user = request.app.state.services.accounts.authenticate(
            username,
            password,
            source_ip=client_ip(request),
            now=now_beijing(),
        )
    except AuthenticationFailed as exc:
        token = anonymous_csrf(request)
        response = _templates(request).TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": token, "error": str(exc)},
            status_code=400,
        )
        _set_form_csrf(request, response, token)
        return response
    grant = request.app.state.services.sessions.create(user.id, now_beijing())
    response = RedirectResponse(_auth_destination(user), 303)
    response.set_cookie(
        SESSION_COOKIE,
        grant.raw_token,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    response.delete_cookie(FORM_CSRF_COOKIE)
    return response


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    session, _user = current_user(request)
    if session is None:
        return RedirectResponse("/login", 303)
    require_csrf(request, csrf_token, session)
    raw = request.cookies.get(SESSION_COOKIE, "")
    request.app.state.services.sessions.invalidate(raw)
    response = RedirectResponse("/login", 303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    session, user = current_user(request)
    if session is None or user is None:
        return RedirectResponse("/login", 303)
    return _templates(request).TemplateResponse(
        request=request,
        name="change_password.html",
        context={"csrf_token": session.csrf_token, "error": None, "user": user},
    )


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user = current_user(request)
    if session is None or user is None:
        return RedirectResponse("/login", 303)
    require_csrf(request, csrf_token, session)
    try:
        request.app.state.services.accounts.change_password(
            user.id,
            current_password,
            new_password,
            now=now_beijing(),
        )
    except (AccountError, ValueError) as exc:
        return _templates(request).TemplateResponse(
            request=request,
            name="change_password.html",
            context={"csrf_token": session.csrf_token, "error": str(exc), "user": user},
            status_code=400,
        )
    grant = request.app.state.services.sessions.create(user.id, now_beijing())
    refreshed = request.app.state.services.accounts.get(user.id)
    response = RedirectResponse(_auth_destination(refreshed), 303)
    response.set_cookie(
        SESSION_COOKIE,
        grant.raw_token,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return response

