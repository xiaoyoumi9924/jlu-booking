"""Token onboarding and private profile routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..credentials import CredentialError, CredentialNotFound
from ..dependencies import current_user, now_beijing, require_csrf
from ..profile_history import list_history
from ..security import mask_secret


router = APIRouter()


def _guard(request: Request, *, allow_pending: bool = False):
    session, user = current_user(request)
    if session is None or user is None:
        return None, None, RedirectResponse("/login", 303)
    if user.must_change_password:
        return None, None, RedirectResponse("/change-password", 303)
    if not allow_pending and user.status == "pending_token":
        return None, None, RedirectResponse("/onboarding/token", 303)
    return session, user, None


def _profile_guard(request: Request, *, for_write: bool = False):
    session, user, redirect = _guard(request)
    if redirect:
        return session, user, redirect
    if user.role != "user" or user.status != "active":
        if for_write:
            raise HTTPException(status_code=403, detail="无权修改个人设置。")
        return None, None, RedirectResponse("/admin" if user.role == "admin" else "/login", 303)
    return session, user, None


@router.get("/onboarding/token", response_class=HTMLResponse)
async def token_onboarding_page(request: Request):
    session, user, redirect = _guard(request, allow_pending=True)
    if redirect:
        return redirect
    if user.status == "active":
        return RedirectResponse("/admin" if user.role == "admin" else "/", 303)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="token_onboarding.html",
        context={"csrf_token": session.csrf_token, "error": None, "user": user},
    )


@router.post("/onboarding/token")
async def token_onboarding(
    request: Request,
    token: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = _guard(request, allow_pending=True)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    try:
        await request.app.state.services.credentials.activate_user_async(
            user.id, token, now=now_beijing()
        )
    except CredentialError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="token_onboarding.html",
            context={"csrf_token": session.csrf_token, "error": str(exc), "user": user},
            status_code=400,
        )
    return RedirectResponse("/", 303)


def _profile_context(request, session, user, error=None, *, page=1):
    credentials = request.app.state.services.credentials
    try:
        masked_token = mask_secret(credentials.decrypt_token(user.id))
    except CredentialNotFound:
        masked_token = "未绑定"
    try:
        companion = credentials.decrypt_companion(user.id)
        companion_name = companion.name
        masked_companion = mask_secret(companion.student_number)
    except CredentialNotFound:
        companion_name = None
        masked_companion = None
    return {
        "csrf_token": session.csrf_token,
        "user": user,
        "masked_token": masked_token,
        "companion_name": companion_name,
        "masked_companion": masked_companion,
        "error": error,
        "history_page": list_history(request.app.state.services.connection, user.id, page=page),
        "history_status_labels": {
            "scheduled": "已排程", "running": "运行中", "success": "预约成功",
            "rejected": "未接受", "unknown": "结果不明", "submission_unknown": "结果不明",
            "cancelled": "已取消", "submitting": "提交中", "no_result": "未找到场地",
            "prechecked": "预检通过", "stopped": "已停止", "error": "运行出错",
        },
        "history_source_labels": {
            "daily": "每日自动", "one_shot": "一次性自动", "manual": "手动",
        },
    }


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, page: int = Query(1, ge=1)):
    session, user, redirect = _profile_guard(request)
    if redirect:
        return redirect
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_profile_context(request, session, user, page=page),
    )


@router.post("/profile/companion")
async def save_companion(
    request: Request,
    student_number: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = _profile_guard(request, for_write=True)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    try:
        await request.app.state.services.credentials.save_companion_async(
            user.id, student_number, now=now_beijing()
        )
    except CredentialError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, session, user, str(exc)),
            status_code=400,
        )
    return RedirectResponse("/profile", 303)


@router.post("/profile/token")
async def replace_token(
    request: Request,
    token: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = _profile_guard(request, for_write=True)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    try:
        await request.app.state.services.credentials.replace_token_async(
            user.id, token, now=now_beijing()
        )
    except CredentialError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=_profile_context(request, session, user, str(exc)),
            status_code=400,
        )
    return RedirectResponse("/profile", 303)
