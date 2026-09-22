"""Audited administrator console and protected credential reveal."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..accounts import AccountError
from ..db import transaction
from ..dependencies import client_ip, current_user, now_beijing, require_csrf
from ..security import mask_secret
from ..tasks import TaskError


router = APIRouter(prefix="/admin")


def _admin(request: Request):
    session, user = current_user(request)
    if session is None or user is None or user.role != "admin" or user.status != "active":
        raise HTTPException(status_code=404, detail="页面不存在。")
    return session, user


def _audit(request, admin, action, target=None, metadata=None):
    request.app.state.services.audit.record(
        admin.id,
        action,
        target,
        client_ip(request),
        metadata or {},
        now=now_beijing(),
    )


@router.get("")
async def dashboard(request: Request):
    session, admin = _admin(request)
    connection = request.app.state.services.connection
    counts = {
        "active": connection.execute("SELECT COUNT(*) FROM users WHERE role='user' AND status='active'").fetchone()[0],
        "pending": connection.execute("SELECT COUNT(*) FROM users WHERE role='user' AND status='pending_token'").fetchone()[0],
        "running": connection.execute("SELECT COUNT(*) FROM booking_tasks WHERE status='running'").fetchone()[0],
    }
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/dashboard.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "counts": counts},
    )


def _user_rows(request):
    services = request.app.state.services
    result = []
    rows = services.connection.execute(
        "SELECT * FROM users WHERE role='user' ORDER BY id"
    ).fetchall()
    for row in rows:
        user = services.accounts._record(row)
        masked_token = "未绑定"
        companion = "未验证"
        try:
            masked_token = mask_secret(services.credentials.decrypt_token(user.id))
        except Exception:
            pass
        try:
            person = services.credentials.decrypt_companion(user.id)
            companion = f"{person.name} · {mask_secret(person.student_number)}"
        except Exception:
            pass
        result.append((user, masked_token, companion))
    return result


@router.get("/users")
async def users(request: Request):
    session, admin = _admin(request)
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/users.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "users": _user_rows(request)},
    )


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int):
    session, admin = _admin(request)
    row = request.app.state.services.connection.execute(
        "SELECT * FROM users WHERE id=? AND role='user'", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在。")
    user = request.app.state.services.accounts._record(row)
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/user_detail.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "target": user},
    )


async def _account_action(request, user_id, csrf_token, action):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    try:
        if action == "disable":
            request.app.state.services.accounts.disable(user_id, now=now)
            audit_action = "user_disabled"
        elif action == "enable":
            request.app.state.services.accounts.enable(user_id, now=now)
            audit_action = "user_enabled"
        else:
            raise HTTPException(404, "页面不存在。")
    except AccountError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(request, admin, audit_action, user_id)
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{user_id}/disable")
async def disable_user(request: Request, user_id: int, csrf_token: str = Form("")):
    return await _account_action(request, user_id, csrf_token, "disable")


@router.post("/users/{user_id}/enable")
async def enable_user(request: Request, user_id: int, csrf_token: str = Form("")):
    return await _account_action(request, user_id, csrf_token, "enable")


@router.post("/users/{user_id}/delete")
async def delete_pending(request: Request, user_id: int, csrf_token: str = Form("")):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    with transaction(request.app.state.services.connection, immediate=True):
        exists = request.app.state.services.connection.execute(
            "SELECT 1 FROM users WHERE id=? AND role='user' AND status='pending_token'",
            (user_id,),
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "页面不存在。")
        _audit(request, admin, "pending_deleted", user_id)
        request.app.state.services.connection.execute(
            "DELETE FROM users WHERE id=?", (user_id,)
        )
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{user_id}/reset-password")
async def reset_password(request: Request, user_id: int, csrf_token: str = Form("")):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    temporary = secrets.token_urlsafe(18)[:16]
    try:
        request.app.state.services.accounts.reset_password(user_id, temporary, now=now_beijing())
    except AccountError as exc:
        raise HTTPException(404, "页面不存在。") from exc
    _audit(request, admin, "password_reset", user_id)
    response = JSONResponse({"temporary_password": temporary})
    response.headers["Cache-Control"] = "no-store, private"
    return response


@router.get("/reauth")
async def reauth_page(request: Request):
    session, admin = _admin(request)
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/reauth.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "error": None},
    )


@router.post("/reauth")
async def reauth(
    request: Request,
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    try:
        request.app.state.services.reauth.grant(
            admin.id, session.id, password, now=now_beijing()
        )
    except (PermissionError, AccountError) as exc:
        return request.app.state.templates.TemplateResponse(
            request=request, name="admin/reauth.html",
            context={"admin": admin, "csrf_token": session.csrf_token, "error": str(exc)},
            status_code=403,
        )
    _audit(request, admin, "admin_reauthenticated")
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{user_id}/token/reveal")
async def reveal_token(request: Request, user_id: int, csrf_token: str = Form("")):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    if not request.app.state.services.reauth.is_valid(admin.id, now, session.id):
        raise HTTPException(403, "请先重新验证管理员密码。")
    try:
        token = request.app.state.services.credentials.reveal_token(
            admin.id, user_id, source_ip=client_ip(request), now=now
        )
    except Exception as exc:
        raise HTTPException(404, "页面不存在。") from exc
    response = JSONResponse({"token": token, "hide_after": 30})
    response.headers["Cache-Control"] = "no-store, private"
    return response


@router.get("/tasks")
async def tasks(request: Request):
    session, admin = _admin(request)
    rows = request.app.state.services.connection.execute(
        "SELECT t.*, u.username FROM booking_tasks t JOIN users u ON u.id=t.user_id "
        "ORDER BY t.id DESC"
    ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/tasks.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "tasks": rows},
    )


@router.post("/tasks/{task_id}/stop")
async def stop_task(request: Request, task_id: int, csrf_token: str = Form("")):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    row = request.app.state.services.connection.execute(
        "SELECT user_id FROM booking_tasks WHERE id=?", (task_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在。")
    try:
        request.app.state.services.tasks.request_stop(
            row["user_id"], task_id, now=now_beijing()
        )
    except TaskError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(request, admin, "task_stop_requested", row["user_id"], {"task_id": str(task_id)})
    return RedirectResponse("/admin/tasks", 303)


@router.get("/audit")
async def audit(request: Request):
    session, admin = _admin(request)
    rows = request.app.state.services.connection.execute(
        "SELECT a.*, u.username AS admin_username FROM audit_events a "
        "JOIN users u ON u.id=a.admin_id ORDER BY a.id DESC LIMIT 500"
    ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/audit.html",
        context={"admin": admin, "csrf_token": session.csrf_token, "events": rows},
    )
