"""User-owned booking task forms, status, stop requests, and log tail."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...api import VENUES
from ...config import DEFAULT_TIME_PRIORITY
from ...run_status import RunStatusError, load_run_status
from ..credentials import CredentialError
from ..dependencies import now_beijing, require_csrf
from ..profile_helpers import require_active_user
from ..security import RateLimitExceeded
from ..tasks import TaskDraft, TaskError, TaskFrozen, TaskNotFound


router = APIRouter(prefix="/tasks")
MUTATION_WINDOW = timedelta(minutes=1)


def _task_or_404(request: Request, user_id: int, task_id: int):
    try:
        return request.app.state.services.tasks.get_for_user(user_id, task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="预约任务不存在。") from exc


def _mutate_allowed(request: Request, user_id: int, now) -> None:
    try:
        request.app.state.services.throttles.consume(
            f"task-mutation:{user_id}",
            limit=20,
            window=MUTATION_WINDOW,
            now=now,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _parse_priority(values: list[str]) -> list[list[str]]:
    priority = []
    for value in values:
        pieces = value.split("|", 1)
        if len(pieces) != 2:
            raise ValueError("时间优先级格式错误。")
        priority.append(pieces)
    return priority


def _draft(user, request, target_day, venue, sport, preferred, priority, mode):
    companion = request.app.state.services.credentials.decrypt_companion(user.id)
    if mode not in {"scan", "real"}:
        raise ValueError("预约模式无效。")
    return TaskDraft(
        target_day=target_day,
        venue=venue,
        sport=sport,
        companion_id=companion.id,
        preferred_court_number=int(preferred),
        time_priority=_parse_priority(priority),
        real_booking_enabled=mode == "real",
    )


async def _validate_companion_input(request: Request, user_id: int, student_number: str, now) -> None:
    if student_number.strip():
        await request.app.state.services.credentials.save_companion_async(
            user_id, student_number, now=now
        )


def _form_context(request, session, user, *, task=None, error=None):
    now = now_beijing()
    execution_date = task.execution_date if task else request.app.state.services.tasks.next_execution_date(now)
    target_day = task.target_day if task else "today"
    target_date = request.app.state.services.tasks.target_date(execution_date, target_day)
    try:
        companion_name = request.app.state.services.credentials.decrypt_companion(user.id).name
    except CredentialError:
        companion_name = None
    priorities = list(task.time_priority) if task else list(DEFAULT_TIME_PRIORITY)
    priorities.extend(item for item in DEFAULT_TIME_PRIORITY if item not in priorities)
    return {
        "csrf_token": session.csrf_token,
        "user": user,
        "task": task,
        "error": error,
        "venues": VENUES,
        "priority_options": priorities,
        "companion_name": companion_name,
        "execution_date": execution_date,
        "target_date": target_date,
    }


def _render_form(request, session, user, *, task=None, error=None, status=200):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="task_form.html",
        context=_form_context(request, session, user, task=task, error=error),
        status_code=status,
    )


@router.get("/new", response_class=HTMLResponse)
async def new_task_page(request: Request):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    return _render_form(request, session, user)


@router.post("/new")
async def create_task(
    request: Request,
    target_day: str = Form(...),
    venue: str = Form(...),
    sport: str = Form(...),
    preferred_court_number: str = Form(...),
    priority: list[str] = Form(...),
    mode: str = Form("scan"),
    student_number: str = Form(""),
    csrf_token: str = Form(""),
):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        await _validate_companion_input(request, user.id, student_number, now)
        task = request.app.state.services.tasks.create(
            user.id,
            _draft(user, request, target_day, venue, sport, preferred_court_number, priority, mode),
            now=now,
        )
    except sqlite3.OperationalError:
        return HTMLResponse("数据库暂时繁忙，请稍后重试。", status_code=503)
    except (TaskError, CredentialError, ValueError) as exc:
        return _render_form(request, session, user, error=str(exc), status=400)
    return RedirectResponse(f"/tasks/{task.id}", 303)


@router.get("/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: int):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    task = _task_or_404(request, user.id, task_id)
    terminal = task.status not in {"scheduled", "running"}
    log_lines = read_private_log_lines(request, task.id, user.id) if terminal else []
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="task_detail.html",
        context={
            "csrf_token": session.csrf_token,
            "user": user,
            "task": task,
            "target_date": request.app.state.services.tasks.target_date(task.execution_date, task.target_day),
            "log_initial_text": "\n".join(log_lines) if log_lines else ("日志暂不可用" if terminal else "等待日志…"),
        },
    )


@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def edit_task_page(request: Request, task_id: int):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    task = _task_or_404(request, user.id, task_id)
    try:
        request.app.state.services.tasks._require_before_cutoff(task, now_beijing())
        if task.status != "scheduled":
            raise TaskFrozen("任务已冻结。")
    except TaskFrozen as exc:
        return HTMLResponse(str(exc), status_code=423)
    return _render_form(request, session, user, task=task)


@router.post("/{task_id}/edit")
async def edit_task(
    request: Request,
    task_id: int,
    target_day: str = Form(...),
    venue: str = Form(...),
    sport: str = Form(...),
    preferred_court_number: str = Form(...),
    priority: list[str] = Form(...),
    mode: str = Form("scan"),
    student_number: str = Form(""),
    csrf_token: str = Form(""),
):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    task = _task_or_404(request, user.id, task_id)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        request.app.state.services.tasks._require_before_cutoff(task, now)
        if task.status != "scheduled":
            raise TaskFrozen("任务已冻结。")
    except TaskFrozen as exc:
        return HTMLResponse(str(exc), status_code=423)
    try:
        await _validate_companion_input(request, user.id, student_number, now)
        request.app.state.services.tasks.update(
            user.id, task_id,
            _draft(user, request, target_day, venue, sport, preferred_court_number, priority, mode),
            now=now,
        )
    except sqlite3.OperationalError:
        return HTMLResponse("数据库暂时繁忙，请稍后重试。", status_code=503)
    except (TaskError, CredentialError, ValueError) as exc:
        return _render_form(request, session, user, task=task, error=str(exc), status=400)
    return RedirectResponse(f"/tasks/{task_id}", 303)


@router.post("/{task_id}/cancel")
async def cancel_task(request: Request, task_id: int, csrf_token: str = Form("")):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    _task_or_404(request, user.id, task_id)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        request.app.state.services.tasks.cancel(user.id, task_id, now=now)
    except sqlite3.OperationalError:
        return HTMLResponse("数据库暂时繁忙，请稍后重试。", status_code=503)
    except TaskError as exc:
        return HTMLResponse(str(exc), status_code=409)
    return RedirectResponse(f"/tasks/{task_id}", 303)


@router.post("/{task_id}/stop")
async def stop_task(request: Request, task_id: int, csrf_token: str = Form("")):
    session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    require_csrf(request, csrf_token, session)
    _task_or_404(request, user.id, task_id)
    now = now_beijing()
    _mutate_allowed(request, user.id, now)
    try:
        request.app.state.services.tasks.request_stop(user.id, task_id, now=now)
    except sqlite3.OperationalError:
        return HTMLResponse("数据库暂时繁忙，请稍后重试。", status_code=503)
    except TaskError as exc:
        return HTMLResponse(str(exc), status_code=409)
    return RedirectResponse(f"/tasks/{task_id}", 303)


def _private_run_path(
    request: Request, task_id: int, column: str
) -> Path | None:
    if column not in {"log_path", "runtime_path"}:
        raise ValueError("unsupported task-run path")
    row = request.app.state.services.connection.execute(
        f"SELECT {column} FROM task_runs WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None or not row[column]:
        return None
    candidate = Path(row[column])
    return _safe_runtime_path(request, candidate)


def _safe_runtime_path(request: Request, candidate: Path) -> Path | None:
    root = Path(request.app.state.settings.runtime_root)
    if root.is_symlink() or candidate.is_symlink():
        return None
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    current = root
    try:
        relative = candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None
    return candidate


def read_private_log_lines(
    request: Request, task_id: int, user_id: int
) -> list[str]:
    log_path = _private_run_path(request, task_id, "log_path")
    if log_path is None or not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[-500:]
        secrets = [
            request.app.state.services.credentials.decrypt_token(user_id),
            request.app.state.services.credentials.decrypt_companion(
                user_id
            ).student_number,
        ]
    except Exception:
        return []
    return [_redact_line(line, secrets) for line in lines]


def read_private_phase(request: Request, task_id: int) -> str | None:
    runtime_path = _private_run_path(request, task_id, "runtime_path")
    if runtime_path is None:
        return None
    state_path = _safe_runtime_path(
        request, runtime_path / "state" / "last_run.json"
    )
    if state_path is None:
        return None
    try:
        state = load_run_status(state_path)
    except (OSError, RunStatusError):
        return None
    return str(state.get("phase")) if state and state.get("phase") else None


@router.get("/{task_id}/status")
async def task_status(request: Request, task_id: int):
    _session, user, redirect = require_active_user(request)
    if redirect:
        return redirect
    task = _task_or_404(request, user.id, task_id)
    phase = read_private_phase(request, task.id)
    lines = read_private_log_lines(request, task.id, user.id)
    return JSONResponse(
        {
            "status": task.status,
            "phase": phase,
            "updated_at": task.updated_at.isoformat(),
            "log_lines": lines,
        }
    )


def _redact_line(line: str, secrets: list[str]) -> str:
    value = line
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value
