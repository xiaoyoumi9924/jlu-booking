"""Owned, explicit manual booking precheck and confirmation pages."""

from __future__ import annotations

from anyio import to_thread
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..dependencies import now_beijing, require_csrf
from ..manual_booking import ManualBookingError
from ..profile_helpers import require_active_user
from .task_routes import _mutate_allowed

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
    _mutate_allowed(request, user.id, now_beijing())
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


@router.post("/submit")
async def submit(
    request: Request,
    attempt_id: str = Form(...),
    nonce: str = Form(...),
    csrf_token: str = Form(""),
):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    _mutate_allowed(request, user.id, now_beijing())
    try:
        outcome = await to_thread.run_sync(
            request.app.state.services.manual_booking.submit,
            user.id, attempt_id, nonce, now_beijing(),
        )
    except ManualBookingError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request, name="manual_confirm.html",
            context={"csrf_token": session.csrf_token, "user": user,
                     "check": None, "error": str(exc)}, status_code=400,
        )
    return RedirectResponse(f"/manual/result/{outcome.attempt_id}", status_code=303)


@router.get("/result/{attempt_id}")
async def result_page(request: Request, attempt_id: str):
    session, user, redirect = _owner(request)
    if redirect:
        return redirect
    try:
        outcome = await to_thread.run_sync(
            request.app.state.services.manual_booking.result_for_user,
            user.id, attempt_id,
        )
    except ManualBookingError as exc:
        raise HTTPException(status_code=404, detail="手动预约记录不存在。") from exc
    return request.app.state.templates.TemplateResponse(
        request=request, name="manual_result.html",
        context={"csrf_token": session.csrf_token, "user": user, "result": outcome},
    )
