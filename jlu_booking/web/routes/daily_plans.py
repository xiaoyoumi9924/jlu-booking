"""User-only daily automatic booking settings and activation switch."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...api import VENUES
from ...config import DEFAULT_TIME_PRIORITY
from ..credentials import CredentialError
from ..dependencies import now_beijing, require_csrf
from ..profile_helpers import require_active_user
from ..tasks import TaskError
from .task_routes import _draft, _mutate_allowed

router = APIRouter(prefix="/daily-plan")


def _owner(request: Request):
    session, user, redirect = require_active_user(request)
    if redirect:
        return None, None, redirect
    if user.role != "user" or user.status != "active":
        raise HTTPException(status_code=404, detail="页面不存在。")
    return session, user, None


def _context(request, session, user, *, error=None):
    services = request.app.state.services
    now = now_beijing()
    execution_date = services.tasks.next_execution_date(now)
    plan = services.daily_plans.get_for_user(user.id)
    target_day = plan.target_day if plan else "today"
    target_date = services.tasks.target_date(execution_date, target_day)
    task = None
    if plan:
        row = services.connection.execute(
            "SELECT * FROM booking_tasks WHERE daily_plan_id=? "
            "AND status='running' ORDER BY id DESC LIMIT 1",
            (plan.id,),
        ).fetchone()
        if row is None:
            row = services.connection.execute(
            "SELECT * FROM booking_tasks WHERE daily_plan_id=? "
            "AND execution_date=? ORDER BY id DESC LIMIT 1",
            (plan.id, execution_date.isoformat()),
            ).fetchone()
        task = services.tasks._record(row) if row else None
    blocking_reason = services.daily_plans.blocking_reason(plan, execution_date) if plan else None
    try:
        companion = services.credentials.decrypt_companion(user.id)
        companion_name = companion.name
    except CredentialError:
        companion_name = None
    if plan and not plan.enabled and task and task.status == "running":
        status_text = "已关闭；当前任务仍在运行"
    elif not plan or not plan.enabled:
        status_text = "未开启"
    elif task and task.status == "running":
        status_text = "运行中"
    elif blocking_reason:
        status_text = blocking_reason
    elif task and task.status == "scheduled":
        status_text = "已排程"
    elif task and task.status != "cancelled":
        status_text = f"最近结果：{task.status}"
    else:
        status_text = "已开启，当前执行日未获得名额"
    return {
        "csrf_token": session.csrf_token, "user": user, "plan": plan, "task": task,
        "error": error, "venues": VENUES, "priority_options": DEFAULT_TIME_PRIORITY,
        "execution_date": execution_date, "target_date": target_date,
        "companion_name": companion_name, "status_text": status_text,
    }


def _render(request, session, user, *, error=None, status=200):
    return request.app.state.templates.TemplateResponse(
        request=request, name="daily_plan.html",
        context=_context(request, session, user, error=error), status_code=status,
    )


@router.get("")
async def daily_plan_page(request: Request):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    return _render(request, session, user)


@router.post("")
async def save_daily_plan(
    request: Request,
    target_day: str = Form(...),
    venue: str = Form(...),
    sport: str = Form(...),
    preferred_court_number: str = Form(...),
    priority: list[str] = Form(...),
    mode: str = Form("scan"),
    confirm_real: str = Form(""),
    csrf_token: str = Form(""),
):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        if mode == "real" and confirm_real != "yes":
            raise ValueError("保存真实预约模式前，请明确确认将提交真实预约。")
        draft = _draft(
            user, request, target_day, venue, sport,
            preferred_court_number, priority, mode,
        )
        request.app.state.services.daily_plans.save(user.id, draft, now=now)
    except (CredentialError, TaskError, ValueError) as exc:
        return _render(request, session, user, error=str(exc), status=400)
    return RedirectResponse("/daily-plan", 303)


@router.post("/enable")
async def enable_daily_plan(
    request: Request,
    confirm_real: str = Form(""),
    csrf_token: str = Form(""),
):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        plan = request.app.state.services.daily_plans.get_for_user(user.id)
        if plan and plan.real_booking_enabled and confirm_real != "yes":
            raise ValueError("开启真实预约前，请明确确认将提交真实预约。")
        request.app.state.services.daily_plans.set_enabled(user.id, True, now=now)
        request.app.state.services.daily_plans.materialize(now)
    except (TaskError, ValueError) as exc:
        return _render(request, session, user, error=str(exc), status=400)
    return RedirectResponse("/daily-plan", 303)


@router.post("/disable")
async def disable_daily_plan(request: Request, csrf_token: str = Form("")):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        request.app.state.services.daily_plans.set_enabled(user.id, False, now=now)
    except (TaskError, ValueError) as exc:
        return _render(request, session, user, error=str(exc), status=400)
    return RedirectResponse("/daily-plan", 303)
