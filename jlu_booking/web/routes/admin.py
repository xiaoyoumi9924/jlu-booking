"""Audited administrator console and protected credential reveal."""

from __future__ import annotations

import json
import secrets
from datetime import date, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ...api import VENUES
from ...config import DEFAULT_TIME_PRIORITY
from ..accounts import AccountError
from ..credentials import CredentialError
from ..db import TASK_STATUSES, transaction
from ..dependencies import client_ip, current_user, now_beijing, require_csrf
from ..security import RateLimitExceeded, mask_name, mask_secret
from ..tasks import SCHEDULER_START, TaskError
from .task_routes import _draft, _redact_line, read_private_log_lines, read_private_phase


router = APIRouter(prefix="/admin")

ISSUE_STATUSES = ("submission_unknown", "token_invalid", "account_blocked", "network_unavailable", "error")

PLAN_TASK_LABELS = {
    "scheduled": "等待启动",
    "running": "运行中",
    "success": "预约成功",
    "no_result": "未找到场地",
    "token_invalid": "Token 失效",
    "account_blocked": "账号受限",
    "daily_limit": "预约已达上限",
    "submission_unknown": "结果待核对",
    "network_unavailable": "网络不可用",
    "stopped": "已停止",
    "error": "运行出错",
    "cancelled": "已取消",
}
PLAN_NOTICES = {
    "saved": "每日预约配置已保存，变更已写入审计记录。",
    "enabled": "每日自动预约已开启，操作已写入审计记录。",
    "disabled": "每日自动预约已关闭，操作已写入审计记录。",
    "unchanged": "没有检测到变更，未写入审计记录。",
    "noop": "当前状态已经如此，未重复写入审计记录。",
}
PLAN_REASON_LIMIT = 200
PLAN_MUTATION_WINDOW = timedelta(minutes=1)


def _filter_value(request: Request, name: str, allowed: set[str]) -> str:
    value = request.query_params.get(name, "")
    return value if value in allowed else ""


def _page(request: Request) -> int:
    try:
        return max(1, min(int(request.query_params.get("page", "1")), 10000))
    except ValueError:
        return 1


def _date_filter(request: Request, name: str) -> str:
    value = request.query_params.get(name, "")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def _days_before(value: date, count: int) -> date:
    return date.fromordinal(max(1, value.toordinal() - count))


def _render(request: Request, session, admin, template: str, **context):
    return request.app.state.templates.TemplateResponse(
        request=request, name=template,
        context={"admin": admin, "csrf_token": session.csrf_token, **context},
    )


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
        "attention": count("SELECT COUNT(*) FROM booking_tasks WHERE status IN ('submission_unknown', 'token_invalid', 'account_blocked', 'network_unavailable', 'error') AND substr(updated_at, 1, 10)=?", (today,)),
    }
    attention_items = connection.execute(
        "SELECT t.id, t.status, t.updated_at, u.username FROM booking_tasks t "
        "JOIN users u ON u.id=t.user_id WHERE t.status IN "
        "('submission_unknown', 'token_invalid', 'account_blocked', 'network_unavailable', 'error') "
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
                 "recent_tasks": recent_tasks,
                 "user_limit": request.app.state.settings.user_limit},
    )


def _user_rows(request):
    services = request.app.state.services
    result = []
    rows = services.connection.execute(
        "SELECT * FROM users WHERE role='user' AND status!='deleted' ORDER BY id"
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
            companion = f"{mask_name(person.name)} · {mask_secret(person.student_number)}"
        except Exception:
            pass
        result.append((user, masked_token, companion))
    return result


@router.get("/users")
async def users(request: Request):
    session, admin = _admin(request)
    connection = request.app.state.services.connection
    rows = _user_rows(request)
    status = _filter_value(request, "status", {"active", "disabled", "pending_token"})
    query = request.query_params.get("q", "").strip()[:60]
    filtered = [row for row in rows if (not status or row[0].status == status)
                and (not query or query.casefold() in row[0].username.casefold())]
    page = _page(request)
    matching_count = len(filtered)
    filtered = filtered[(page - 1) * 20:page * 20]
    usage = {row["id"]: row for row in connection.execute(
        "SELECT u.id, (SELECT COUNT(*) FROM booking_tasks t WHERE t.user_id=u.id) task_count, "
        "(SELECT MAX(s.last_seen_at) FROM web_sessions s WHERE s.user_id=u.id) last_seen "
        "FROM users u WHERE u.role='user'"
    )}
    return _render(request, session, admin, "admin/users.html", users=filtered,
                   status=status, query=query, page=page, matching_count=matching_count,
                   usage=usage, totals={
                       "all": sum(user.status != "deleted" for user, *_ in rows),
                       "active": sum(user.status == "active" for user, *_ in rows),
                       "bound": connection.execute("SELECT COUNT(*) FROM user_credentials c JOIN users u ON u.id=c.user_id WHERE u.role='user' AND u.status!='deleted'").fetchone()[0],
                       "pending": sum(user.status == "pending_token" for user, *_ in rows),
                   })


def _target_user(request: Request, user_id: int):
    row = request.app.state.services.connection.execute(
        "SELECT * FROM users WHERE id=? AND role='user'", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在。")
    return request.app.state.services.accounts._record(row)


def _user_detail_context(request, session, admin, user_id, *, error=None, notice=None):
    services = request.app.state.services
    connection = services.connection
    row = connection.execute(
        "SELECT * FROM users WHERE id=? AND role='user'", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "页面不存在。")
    user = services.accounts._record(row)
    now = now_beijing()
    credential = connection.execute(
        "SELECT verified_at, last_status FROM user_credentials WHERE user_id=?",
        (user_id,),
    ).fetchone()
    companion = connection.execute(
        "SELECT verified_at FROM companions WHERE user_id=?", (user_id,)
    ).fetchone()
    masked_companion_name = None
    masked_student_number = None
    try:
        person = services.credentials.decrypt_companion(user_id)
    except CredentialError:
        person = None
    if person is not None:
        masked_companion_name = mask_name(person.name)
        masked_student_number = mask_secret(person.student_number)
    activity = connection.execute(
        "SELECT MAX(last_seen_at) FROM web_sessions WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    user_tasks = connection.execute(
        "SELECT id, venue, sport, execution_date, status, source FROM booking_tasks "
        "WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)
    ).fetchall()
    plan_row = connection.execute(
        "SELECT p.*, eb.username AS enabled_by_username, ub.username AS updated_by_username "
        "FROM daily_booking_plans p "
        "LEFT JOIN users eb ON eb.id = p.enabled_by_user_id "
        "LEFT JOIN users ub ON ub.id = p.updated_by_user_id "
        "WHERE p.user_id = ?", (user_id,)
    ).fetchone()
    plan = services.daily_plans._record(plan_row) if plan_row is not None else None
    today_task = None
    if plan is not None:
        today_task = connection.execute(
            "SELECT id, status FROM booking_tasks WHERE daily_plan_id=? "
            "AND (status='running' OR execution_date=?) "
            "ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END, id DESC LIMIT 1",
            (plan.id, now.date().isoformat()),
        ).fetchone()
    next_run = None
    if plan is not None and plan.enabled:
        next_run = "今天 07:27" if now.time() < SCHEDULER_START else "明天 07:27"
    plan_blocking = None
    if plan is not None:
        plan_blocking = services.daily_plans.blocking_reason(
            plan, services.tasks.next_execution_date(now)
        )
    priorities = list(plan.time_priority) if plan is not None else []
    priorities.extend(item for item in DEFAULT_TIME_PRIORITY if item not in priorities)
    return {
        "target": user,
        "credential": credential,
        "companion": companion,
        "masked_companion_name": masked_companion_name,
        "masked_student_number": masked_student_number,
        "activity": activity,
        "user_tasks": user_tasks,
        "plan": plan,
        "plan_enabled_by": plan_row["enabled_by_username"] if plan_row is not None else None,
        "plan_updated_by": plan_row["updated_by_username"] if plan_row is not None else None,
        "today_task": today_task,
        "today_task_label": (
            PLAN_TASK_LABELS.get(today_task["status"], today_task["status"])
            if today_task is not None else None
        ),
        "next_run": next_run,
        "plan_blocking": plan_blocking,
        "venues": VENUES,
        "priority_options": priorities,
        "error": error,
        "notice": PLAN_NOTICES.get(notice) if notice else None,
    }


def _render_user_detail(request, session, admin, user_id, *, error=None, notice=None, status=200):
    context = _user_detail_context(
        request, session, admin, user_id, error=error, notice=notice
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/user_detail.html",
        context={"admin": admin, "csrf_token": session.csrf_token, **context},
        status_code=status,
    )


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int):
    session, admin = _admin(request)
    return _render_user_detail(
        request, session, admin, user_id,
        notice=request.query_params.get("notice", ""),
    )


def _require_reason(value: str) -> str:
    reason = str(value or "").strip()
    if not reason:
        raise ValueError("请填写操作原因。")
    if len(reason) > PLAN_REASON_LIMIT:
        raise ValueError(f"操作原因不能超过 {PLAN_REASON_LIMIT} 字。")
    return reason


def _plan_mutation_allowed(request: Request, admin_id: int, now) -> None:
    try:
        request.app.state.services.throttles.consume(
            f"admin-plan:{int(admin_id)}",
            limit=20,
            window=PLAN_MUTATION_WINDOW,
            now=now,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _plan_snapshot(plan) -> dict:
    return {
        "target_day": plan.target_day,
        "venue": plan.venue,
        "sport": plan.sport,
        "preferred_court_number": plan.preferred_court_number,
        "time_priority": [list(item) for item in plan.time_priority],
        "real_booking_enabled": bool(plan.real_booking_enabled),
    }


def _audit_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _changed_fields(plan, values: dict) -> list[str]:
    changed = []
    if plan.target_day != values["target_day"]:
        changed.append("target_day")
    if plan.venue != values["venue"]:
        changed.append("venue")
    if plan.sport != values["sport"]:
        changed.append("sport")
    if int(plan.companion_id) != int(values["companion_id"]):
        changed.append("companion_id")
    if int(plan.preferred_court_number) != int(values["preferred_court_number"]):
        changed.append("preferred_court_number")
    if [list(item) for item in plan.time_priority] != json.loads(values["time_priority_json"]):
        changed.append("time_priority")
    if int(plan.real_booking_enabled) != int(values["real_booking_enabled"]):
        changed.append("real_booking_enabled")
    return changed


@router.post("/users/{user_id}/daily-plan")
async def admin_save_daily_plan(
    request: Request,
    user_id: int,
    target_day: str = Form(...),
    venue: str = Form(...),
    sport: str = Form(...),
    preferred_court_number: str = Form(...),
    priority: list[str] = Form(...),
    mode: str = Form("scan"),
    reason: str = Form(""),
    csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _plan_mutation_allowed(request, admin.id, now)
    target = _target_user(request, user_id)
    services = request.app.state.services
    try:
        reason_text = _require_reason(reason)
        draft = _draft(
            target, request, target_day, venue, sport,
            preferred_court_number, priority, mode,
        )
        current = services.daily_plans.get_for_user(user_id)
        values = services.tasks._normalize_draft(user_id, draft)
        changed = _changed_fields(current, values) if current is not None else ["created"]
        if current is not None and not changed:
            return RedirectResponse(f"/admin/users/{user_id}?notice=unchanged", 303)
        before = _audit_json(_plan_snapshot(current)) if current is not None else ""
        def _record_plan_change(plan) -> None:
            _audit(request, admin, "daily_plan_updated", user_id, {
                "outcome": "success",
                "reason": reason_text,
                "before": before,
                "after": _audit_json(_plan_snapshot(plan)),
                "changed_fields": ",".join(changed),
            })
        services.daily_plans.save(
            user_id, draft, now=now, actor_id=admin.id,
            on_success=_record_plan_change,
        )
    except (CredentialError, TaskError, ValueError) as exc:
        return _render_user_detail(
            request, session, admin, user_id, error=str(exc), status=400
        )
    return RedirectResponse(f"/admin/users/{user_id}?notice=saved", 303)


@router.post("/users/{user_id}/daily-plan/enable")
async def admin_enable_daily_plan(
    request: Request,
    user_id: int,
    reason: str = Form(""),
    confirm_real: str = Form(""),
    csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _plan_mutation_allowed(request, admin.id, now)
    _target_user(request, user_id)
    services = request.app.state.services
    try:
        reason_text = _require_reason(reason)
        plan = services.daily_plans.get_for_user(user_id)
        if plan is None:
            raise ValueError("该用户还没有每日预约配置，请先保存配置。")
        if plan.enabled:
            return RedirectResponse(f"/admin/users/{user_id}?notice=noop", 303)
        if plan.real_booking_enabled and confirm_real != "yes":
            raise ValueError("开启真实预约前，请勾选确认将向学校系统提交真实预约。")
        def _record_enable(plan) -> None:
            _audit(request, admin, "daily_plan_enabled", user_id, {
                "outcome": "success",
                "reason": reason_text,
                "plan_snapshot": _audit_json(_plan_snapshot(plan)),
                "enabled_at": plan.enabled_at.isoformat() if plan.enabled_at else "",
            })
        services.daily_plans.set_enabled(
            user_id, True, now=now, actor_id=admin.id,
            on_success=_record_enable,
        )
        services.daily_plans.materialize(now)
    except (CredentialError, TaskError, ValueError) as exc:
        return _render_user_detail(
            request, session, admin, user_id, error=str(exc), status=400
        )
    return RedirectResponse(f"/admin/users/{user_id}?notice=enabled", 303)


@router.post("/users/{user_id}/daily-plan/disable")
async def admin_disable_daily_plan(
    request: Request,
    user_id: int,
    reason: str = Form(""),
    csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _plan_mutation_allowed(request, admin.id, now)
    _target_user(request, user_id)
    services = request.app.state.services
    try:
        reason_text = _require_reason(reason)
        plan = services.daily_plans.get_for_user(user_id)
        if plan is None:
            raise ValueError("该用户还没有每日预约配置。")
        if not plan.enabled:
            return RedirectResponse(f"/admin/users/{user_id}?notice=noop", 303)
        cancelled = [
            row["id"] for row in services.connection.execute(
                "SELECT id FROM booking_tasks WHERE daily_plan_id=? AND status='scheduled' "
                "ORDER BY id", (plan.id,),
            )
        ]
        def _record_disable(_plan) -> None:
            _audit(request, admin, "daily_plan_disabled", user_id, {
                "outcome": "success",
                "reason": reason_text,
                "cancelled_task_ids": ",".join(str(task_id) for task_id in cancelled),
            })
        services.daily_plans.set_enabled(
            user_id, False, now=now, actor_id=admin.id,
            on_success=_record_disable,
        )
    except (CredentialError, TaskError, ValueError) as exc:
        return _render_user_detail(
            request, session, admin, user_id, error=str(exc), status=400
        )
    return RedirectResponse(f"/admin/users/{user_id}?notice=disabled", 303)


@router.post("/users/{user_id}/companion/reveal")
async def reveal_companion(
    request: Request, user_id: int,
    password: str = Form(""), csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    if not password:
        raise HTTPException(403, "请输入管理员密码。")
    try:
        await request.app.state.services.reauth.grant_async(
            admin.id, session.id, password, now=now,
            on_success=lambda: _audit(
                request, admin, "admin_reauthenticated",
                metadata={"outcome": "success"},
            ),
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except (PermissionError, AccountError) as exc:
        _audit(
            request, admin, "admin_reauthentication_failed",
            metadata={"outcome": "failure", "reason": type(exc).__name__},
        )
        raise HTTPException(403, str(exc)) from exc
    try:
        person = request.app.state.services.credentials.decrypt_companion(user_id)
    except CredentialError as exc:
        raise HTTPException(404, "该用户尚未验证同行人。") from exc
    _audit(request, admin, "companion_revealed", user_id, {
        "outcome": "success",
        "fields": "name,student_id",
        "reauth": "true",
    })
    response = JSONResponse({
        "name": person.name,
        "student_number": person.student_number,
        "hide_after": 60,
    })
    response.headers["Cache-Control"] = "no-store, private"
    return response


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


@router.post("/users/{user_id}/token/reveal")
async def reveal_token(
    request: Request, user_id: int,
    password: str = Form(""), csrf_token: str = Form(""),
):
    session, admin = _admin(request)
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    if not password:
        raise HTTPException(403, "请输入管理员密码。")
    try:
        await request.app.state.services.reauth.grant_async(
            admin.id, session.id, password, now=now,
            on_success=lambda: _audit(
                request, admin, "admin_reauthenticated",
                metadata={"outcome": "success"},
            ),
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except (PermissionError, AccountError) as exc:
        _audit(
            request, admin, "admin_reauthentication_failed",
            metadata={"outcome": "failure", "reason": type(exc).__name__},
        )
        raise HTTPException(403, str(exc)) from exc
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
    connection = request.app.state.services.connection
    status = _filter_value(request, "status", set(TASK_STATUSES))
    venue = _filter_value(request, "venue", {"前卫体育馆", "宋治平体育馆"})
    query = request.query_params.get("q", "").strip()[:60]
    day = _date_filter(request, "day")
    where = ("FROM booking_tasks t JOIN users u ON u.id=t.user_id "
             "WHERE (?='' OR t.status=?) AND (?='' OR t.venue=?) AND (?='' OR t.execution_date=?) "
             "AND (?='' OR u.username LIKE ?)")
    params = (status, status, venue, venue, day, day, query, f"%{query}%")
    page = _page(request)
    total = connection.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
    rows = connection.execute(
        "SELECT t.*, u.username FROM booking_tasks t JOIN users u ON u.id=t.user_id "
        "WHERE (?='' OR t.status=?) AND (?='' OR t.venue=?) AND (?='' OR t.execution_date=?) "
        "AND (?='' OR u.username LIKE ?) ORDER BY t.id DESC LIMIT 30 OFFSET ?",
        (*params, (page - 1) * 30),
    ).fetchall()
    return _render(request, session, admin, "admin/tasks.html", tasks=rows,
                   status=status, venue=venue, query=query, day=day,
                   page=page, total=total)


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
    if run is not None:
        run = dict(run)
        try:
            secrets_to_hide = [
                services.credentials.decrypt_token(task.user_id),
                services.credentials.decrypt_companion(task.user_id).student_number,
            ]
            run["detail"] = _redact_line(run["detail"] or "", secrets_to_hide)
        except Exception:
            run["detail"] = ""
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
    connection = request.app.state.services.connection
    action = request.query_params.get("action", "").strip()[:60]
    allowed = {row[0] for row in connection.execute("SELECT DISTINCT action FROM audit_events")}
    if action not in allowed:
        action = ""
    page = _page(request)
    total = connection.execute("SELECT COUNT(*) FROM audit_events WHERE (?='' OR action=?)", (action, action)).fetchone()[0]
    rows = connection.execute(
        "SELECT a.*, u.username AS admin_username, t.username AS target_username "
        "FROM audit_events a JOIN users u ON u.id=a.admin_id "
        "LEFT JOIN users t ON t.id=a.target_user_id "
        "WHERE (?='' OR a.action=?) ORDER BY a.id DESC LIMIT 30 OFFSET ?",
        (action, action, (page - 1) * 30),
    ).fetchall()
    return _render(request, session, admin, "admin/audit.html", events=rows,
                   action=action, actions=sorted(allowed), page=page, total=total)


@router.get("/exceptions")
async def exceptions(request: Request):
    session, admin = _admin(request)
    connection = request.app.state.services.connection
    status = _filter_value(request, "status", set(ISSUE_STATUSES))
    query = request.query_params.get("q", "").strip()[:60]
    day = _date_filter(request, "day")
    page = _page(request)
    where = ("FROM booking_tasks t JOIN users u ON u.id=t.user_id "
             "WHERE t.status IN ('submission_unknown','token_invalid','account_blocked','network_unavailable','error') "
             "AND (?='' OR t.status=?) AND (?='' OR u.username LIKE ?) "
             "AND (?='' OR substr(t.updated_at,1,10)=?)")
    params = (status, status, query, f"%{query}%", day, day)
    filtered_total = connection.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
    rows = connection.execute(
        "SELECT t.id, t.status, t.updated_at, t.venue, t.sport, u.username "
        + where + " ORDER BY t.updated_at DESC LIMIT 30 OFFSET ?",
        (*params, (page-1)*30),
    ).fetchall()
    counts = {row["status"]: row["n"] for row in connection.execute(
        "SELECT status, COUNT(*) n FROM booking_tasks WHERE status IN "
        "('submission_unknown','token_invalid','account_blocked','network_unavailable','error') GROUP BY status"
    )}
    return _render(request, session, admin, "admin/exceptions.html", items=rows,
                   status=status, query=query, day=day, page=page,
                   filtered_total=filtered_total, counts=counts,
                   total=sum(counts.values()))


@router.get("/stats")
async def stats(request: Request):
    session, admin = _admin(request)
    connection = request.app.state.services.connection
    today = now_beijing().date()
    try:
        end = date.fromisoformat(request.query_params.get("end", ""))
    except ValueError:
        end = today
    try:
        start = date.fromisoformat(request.query_params.get("start", ""))
    except ValueError:
        start = _days_before(end, 6)
    if start > end:
        start = _days_before(end, 6)
    if (end - start).days > 30:
        start = _days_before(end, 30)
    sport_options = [row[0] for row in connection.execute(
        "SELECT DISTINCT sport FROM booking_tasks ORDER BY sport"
    )]
    sport = request.query_params.get("sport", "")
    if sport not in sport_options:
        sport = ""
    user_options = connection.execute(
        "SELECT id, username FROM users WHERE role='user' AND status!='deleted' ORDER BY username"
    ).fetchall()
    try:
        user_id = int(request.query_params.get("user_id", "0"))
    except ValueError:
        user_id = 0
    if user_id not in {row["id"] for row in user_options}:
        user_id = 0
    where = (
        "FROM booking_tasks t JOIN users u ON u.id=t.user_id "
        "WHERE substr(t.created_at,1,10) BETWEEN ? AND ? "
        "AND (?='' OR t.sport=?) AND (?=0 OR t.user_id=?)"
    )
    params = (start.isoformat(), end.isoformat(), sport, sport, user_id, user_id)
    days = [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]
    daily = {day: 0 for day in days}
    for row in connection.execute(
        "SELECT substr(t.created_at,1,10) day, COUNT(*) n " + where +
        " GROUP BY day", params,
    ):
        if row["day"] in daily:
            daily[row["day"]] = row["n"]
    sports = connection.execute(
        "SELECT t.sport, COUNT(*) total " + where +
        " GROUP BY t.sport ORDER BY total DESC, t.sport", params,
    ).fetchall()
    active_users = connection.execute(
        "SELECT u.username, COUNT(*) total " + where +
        " GROUP BY t.user_id ORDER BY total DESC, u.username LIMIT 10", params,
    ).fetchall()
    totals = connection.execute(
        "SELECT COUNT(*) total, COALESCE(SUM(t.status='success'),0) success, "
        "COUNT(DISTINCT t.user_id) users " + where, params,
    ).fetchone()
    return _render(
        request, session, admin, "admin/stats.html", daily=daily,
        sports=sports, active_users=active_users, totals=totals,
        success_rate=round(totals["success"] / totals["total"] * 100)
        if totals["total"] else 0,
        max_daily=max([1, *daily.values()]), start=start.isoformat(),
        end=end.isoformat(), sport=sport, user_id=user_id,
        sport_options=sport_options, user_options=user_options,
    )
