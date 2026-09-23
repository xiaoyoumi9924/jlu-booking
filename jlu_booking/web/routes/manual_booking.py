"""Owned, explicit manual booking precheck and confirmation pages."""

from __future__ import annotations

from anyio import to_thread
from fastapi import APIRouter, Form, HTTPException, Request

from ..dependencies import now_beijing, require_csrf
from ..manual_booking import ManualBookingError
from ..profile_helpers import require_active_user

router = APIRouter(prefix="/manual")


def _owner(request: Request):
    session, user, redirect = require_active_user(request)
    if redirect:
        return None, None, redirect
    if user.role != "user" or user.status != "active":
        raise HTTPException(status_code=404, detail="页面不存在。")
    return session, user, None


@router.post("/precheck")
async def precheck(
    request: Request,
    candidate_id: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    try:
        check = await to_thread.run_sync(
            request.app.state.services.manual_booking.precheck,
            user.id, candidate_id, now_beijing(),
        )
        error = None
        status = 200
    except ManualBookingError as exc:
        check = None
        error = str(exc)
        status = 400
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="manual_confirm.html",
        context={"csrf_token": session.csrf_token, "user": user,
                 "check": check, "error": error},
        status_code=status,
    )
