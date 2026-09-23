"""Authenticated user dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..dependencies import current_user, now_beijing
from ..security import mask_secret


router = APIRouter()


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
    try:
        token_masked = mask_secret(services.credentials.decrypt_token(user.id))
        companion = services.credentials.decrypt_companion(user.id)
        companion_text = f"{companion.name} · {mask_secret(companion.student_number)}"
    except Exception:
        token_masked = "未绑定"
        companion_text = "未验证"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "execution_date": execution_date,
            "remaining": max(0, request.app.state.settings.daily_task_limit - used),
            "active_task": active_task,
            "latest_task": latest_task,
            "token_masked": token_masked,
            "companion_text": companion_text,
        },
    )
