"""Authenticated user dashboard."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ...api import VENUES
from ..dependencies import current_user, now_beijing
from ..security import mask_secret


router = APIRouter()


def dashboard_context(request, session, user, *, result=None, error=None, selection=None):
    services = request.app.state.services
    now = now_beijing()
    execution_date = services.tasks.next_execution_date(now)
    used = services.connection.execute(
        "SELECT COUNT(*) FROM booking_tasks WHERE execution_date = ? "
        "AND status != 'cancelled'",
        (execution_date.isoformat(),),
    ).fetchone()[0]
    active_row = services.connection.execute(
        "SELECT * FROM booking_tasks WHERE user_id = ? "
        "AND status IN ('scheduled', 'running') ORDER BY id DESC LIMIT 1",
        (user.id,),
    ).fetchone()
    latest_row = services.connection.execute(
        "SELECT * FROM booking_tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user.id,),
    ).fetchone()
    active_task = services.tasks._record(active_row) if active_row else None
    latest_task = services.tasks._record(latest_row) if latest_row else None
    daily_plan = services.daily_plans.get_for_user(user.id)
    daily_status = "未开启"
    daily_attention = False
    if daily_plan:
        blocking_reason = services.daily_plans.blocking_reason(daily_plan, execution_date)
        running_row = services.connection.execute(
            "SELECT status FROM booking_tasks WHERE daily_plan_id=? "
            "AND status='running' ORDER BY id DESC LIMIT 1",
            (daily_plan.id,),
        ).fetchone()
        daily_row = services.connection.execute(
            "SELECT status FROM booking_tasks WHERE daily_plan_id=? "
            "AND execution_date=? ORDER BY id DESC LIMIT 1",
            (daily_plan.id, execution_date.isoformat()),
        ).fetchone()
        if running_row:
            daily_status = "运行中" if daily_plan.enabled else "已关闭；当前任务仍在运行"
            daily_attention = not daily_plan.enabled
        elif not daily_plan.enabled:
            daily_status = "未开启"
        elif blocking_reason:
            daily_status = blocking_reason
            daily_attention = True
        elif daily_row and daily_row["status"] == "scheduled":
            daily_status = "已排程"
        elif daily_row and daily_row["status"] == "running":
            daily_status = "运行中"
        elif daily_row and daily_row["status"] != "cancelled":
            daily_status = f"最近结果：{daily_row['status']}"
        else:
            daily_status = "已开启，当前执行日未获得名额"
    try:
        token_masked = mask_secret(services.credentials.decrypt_token(user.id))
    except Exception:
        token_masked = "未绑定"
    try:
        companion = services.credentials.decrypt_companion(user.id)
        companion_text = f"{companion.name} · {mask_secret(companion.student_number)}"
    except Exception:
        companion_text = "未验证"
    grouped_slots = {}
    if result is not None:
        for slot in sorted(
            result.slots,
            key=lambda item: (str(item["court_name"]), str(item["start"])),
        ):
            grouped_slots.setdefault(str(slot["court_name"]), []).append(slot)
    requested_venue = selection[0] if selection else request.query_params.get("venue")
    selected_venue = result.venue if result else (
        requested_venue if requested_venue in VENUES else next(iter(VENUES))
    )
    requested_sport = selection[1] if selection else None
    available_sports = VENUES[selected_venue]["sports"]
    selected_sport = result.sport if result else (
        requested_sport if requested_sport in available_sports else next(iter(available_sports))
    )
    requested_day = selection[2] if selection else None
    selected_day = (
        "tomorrow" if result and result.query_date == (now.date() + timedelta(days=1)).isoformat()
        else "today" if result else requested_day if requested_day in {"today", "tomorrow"} else "today"
    )
    return {
        "user": user,
        "csrf_token": session.csrf_token,
        "execution_date": execution_date,
        "remaining": max(0, request.app.state.settings.daily_task_limit - used),
        "active_task": active_task,
        "latest_task": latest_task,
        "daily_status": daily_status,
        "daily_attention": daily_attention,
        "token_masked": token_masked,
        "companion_text": companion_text,
        "venues": VENUES,
        "availability_result": result,
        "query_error": error,
        "grouped_slots": grouped_slots,
        "selected_venue": selected_venue,
        "selected_sport": selected_sport,
        "selected_day": selected_day,
        "today_label": now.date().strftime("%m月%d日"),
        "tomorrow_label": (now.date() + timedelta(days=1)).strftime("%m月%d日"),
        "court_count": len(grouped_slots),
        "slot_count": len(result.slots) if result else 0,
    }


@router.get("/")
async def dashboard(request: Request):
    session, user = current_user(request)
    if session is None or user is None:
        return RedirectResponse("/login", 303)
    if user.must_change_password:
        return RedirectResponse("/change-password", 303)
    if user.status == "pending_token":
        return RedirectResponse("/onboarding/token", 303)
    if user.role == "admin":
        return RedirectResponse("/admin", 303)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=dashboard_context(request, session, user),
    )
