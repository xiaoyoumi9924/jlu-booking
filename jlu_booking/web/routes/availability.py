"""Authenticated read-only school availability query endpoint."""

from __future__ import annotations

from dataclasses import replace

from anyio import to_thread
from fastapi import APIRouter, Form, HTTPException, Request

from ..availability import AvailabilityQueryError
from ..manual_booking import ManualBookingError
from ..dependencies import now_beijing, require_csrf
from ..profile_helpers import require_active_user
from ..security import RateLimitExceeded
from .dashboard import dashboard_context


router = APIRouter(prefix="/availability")


@router.post("/query")
async def query_availability(
    request: Request,
    venue: str = Form(...),
    sport: str = Form(...),
    target_day: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    if user.role != "user" or user.status != "active":
        raise HTTPException(status_code=404, detail="页面不存在。")
    require_csrf(request, csrf_token, session)
    try:
        result = await to_thread.run_sync(
            request.app.state.services.availability.query,
            user.id,
            venue,
            sport,
            target_day,
            now_beijing(),
        )
        candidates = await to_thread.run_sync(
            request.app.state.services.manual_booking.register_candidates,
            user.id,
            result,
        )
        result = replace(result, slots=tuple(candidates))
        error = None
        status = 200
    except RateLimitExceeded:
        raise
    except (AvailabilityQueryError, ManualBookingError, ValueError) as exc:
        result = None
        error = str(exc)
        status = 400
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=dashboard_context(
            request, session, user, result=result, error=error,
            selection=(venue, sport, target_day),
        ),
        status_code=status,
    )
