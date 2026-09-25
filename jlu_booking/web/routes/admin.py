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
from .task_routes import read_private_log_lines, read_private_phase


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
    today = now_beijing().date().isoformat()
    def count(query: str, params: tuple = ()) -> int:
        return int(connection.execute(query, params).fetchone()[0])

    counts = {
        "users": count("SELECT COUNT(*) FROM users WHERE role='user' AND status!='deleted'"),
        "active": count("SELECT COUNT(*) FROM users WHERE role='user' AND status='active'"),
        "pending": count("SELECT COUNT(*) FROM users WHERE role='user' AND status='pending_token'"),
        "bound": count("SELECT COUNT(*) FROM user_credentials c JOIN users u ON u.id=c.user_id WHERE u.role='user' AND u.status!='deleted'"),
        "running": count("SELECT COUNT(*) FROM booking_tasks WHERE status='running'"),
        "scheduled": count("SELECT COUNT(*) FROM booking_tasks WHERE status='scheduled'"),
        "today_success": count("SELECT COUNT(*) FROM booking_tasks WHERE target_day='today' AND execution_date=? AND status='success'", (today,))
        + count("SELECT COUNT(*) FROM booking_tasks WHERE target_day='tomorrow' AND date(execution_date, '+1 day')=? AND status='success'", (today,))
        + count("SELECT COUNT(*) FROM manual_booking_attempts a JOIN manual_candidates c ON c.id=a.candidate_id WHERE c.query_date=? AND a.status='success'", (today,)),
        "attention": count("SELECT COUNT(*) FROM booking_tasks WHERE status IN ('submission_unknown', 'token_invalid', 'account_blocked', 'error') AND substr(updated_at, 1, 10)=?", (today,)),
    }
    attention_items = connection.execute(
        "SELECT t.id, t.status, t.updated_at, u.username FROM booking_tasks t "
        "JOIN users u ON u.id=t.user_id WHERE t.status IN "
        "('submission_unknown', 'token_invalid', 'account_blocked', 'error') "
        "AND substr(t.updated_at, 1, 10)=? ORDER BY t.updated_at DESC LIMIT 3", (today,)
    ).fetchall()
    recent_tasks = connection.execute(
        "SELECT t.id, t.venue, t.sport, t.execution_date, t.target_day, "
        "t.status, t.updated_at, u.username FROM booking_tasks t "
        "JOIN users u ON u.id=t.user_id ORDER BY t.created_at DESC, t.id DESC LIMIT 5"
    ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/dashboard.html",
        context={"admin": admin, "csrf_token": session.csrf_token,
                 "counts": counts, "attention_items": attention_items,
                 "recent_tasks": recent_tasks},
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
    if action == "disable":
        audit_action = "user_disabled"
        service_action = request.app.state.services.accounts.disable
    elif action == "enable":
        audit_action = "user_enabled"
        service_action = request.app.state.services.accounts.enable
    else:
        raise HTTPException(404, "页面不存在。")
    try:
        service_action(
            user_id,
            now=now,
            on_success=lambda: _audit(
                request,
                admin,
                audit_action,
                user_id,
                {"outcome": "success"},
            ),
        )
    except AccountError as exc:
        _audit(
            request,
            admin,
            audit_action,
            None,
            {"outcome": "failure", "reason": type(exc).__name__},
        )
        raise HTTPException(409, str(exc)) from exc
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
    try:
        with transaction(request.app.state.services.connection, immediate=True):
            exists = request.app.state.services.connection.execute(
                "SELECT username FROM users WHERE id=? AND role='user' "
                "AND status='pending_token'",
                (user_id,),
            ).fetchone()
            if exists is None:
                raise LookupError("pending user not found")
            _audit(
                request,
                admin,
                "pending_deleted",
                user_id,
                {"outcome": "success", "target_username": exists["username"]},
            )
            request.app.state.services.connection.execute(
                "DELETE FROM users WHERE id=?", (user_id,)
            )
    except LookupError as exc:
        _audit(
            request,
            admin,
            "pending_deleted",
            None,
            {"outcome": "failure", "reason": "UserNotFound"},
        )
        raise HTTPException(404, "页面不存在。") from exc
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{user_id}/reset-password")
async def reset_password(request: Request, user_id: int, csrf_token: str = Form("")):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    temporary = secrets.token_urlsafe(18)[:16]
    try:
        await request.app.state.services.accounts.reset_password_async(
            user_id,
            temporary,
            now=now_beijing(),
            on_success=lambda: _audit(
                request,
                admin,
                "password_reset",
                user_id,
                {"outcome": "success"},
            ),
        )
    except AccountError as exc:
        _audit(
            request,
            admin,
            "password_reset",
            None,
            {"outcome": "failure", "reason": type(exc).__name__},
        )
        raise HTTPException(404, "页面不存在。") from exc
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
        await request.app.state.services.reauth.grant_async(
            admin.id,
            session.id,
            password,
            now=now_beijing(),
            on_success=lambda: _audit(
                request,
                admin,
                "admin_reauthenticated",
                metadata={"outcome": "success"},
            ),
        )
    except (PermissionError, AccountError) as exc:
        _audit(
            request,
            admin,
            "admin_reauthentication_failed",
            metadata={"outcome": "failure", "reason": type(exc).__name__},
        )
        return request.app.state.templates.TemplateResponse(
            request=request, name="admin/reauth.html",
            context={"admin": admin, "csrf_token": session.csrf_token, "error": str(exc)},
            status_code=403,
        )
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


@router.get("/tasks/{task_id}")
async def task_detail(request: Request, task_id: int):
    session, admin = _admin(request)
    services = request.app.state.services
    row = services.connection.execute(
        "SELECT t.*, u.username FROM booking_tasks t "
        "JOIN users u ON u.id=t.user_id WHERE t.id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在。")
    task = services.tasks._record(row)
    run = services.connection.execute(
        "SELECT started_at, finished_at, exit_code, final_status, detail "
        "FROM task_runs WHERE task_id=?",
        (task_id,),
    ).fetchone()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/task_detail.html",
        context={
            "admin": admin,
            "csrf_token": session.csrf_token,
            "task": task,
            "username": row["username"],
            "run": run,
            "phase": read_private_phase(request, task_id),
            "log_lines": read_private_log_lines(request, task_id, task.user_id),
        },
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
            row["user_id"],
            task_id,
            now=now_beijing(),
            on_success=lambda: _audit(
                request,
                admin,
                "task_stop_requested",
                row["user_id"],
                {"task_id": str(task_id), "outcome": "success"},
            ),
        )
    except TaskError as exc:
        _audit(
            request,
            admin,
            "task_stop_requested",
            row["user_id"],
            {
                "task_id": str(task_id),
                "outcome": "failure",
                "reason": type(exc).__name__,
            },
        )
        raise HTTPException(409, str(exc)) from exc
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
