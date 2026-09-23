"""FastAPI application factory for the optional browser interface."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .accounts import AccountService, ActiveUserLimitReached
from .availability import AvailabilityService
from .audit import AuditService, ReauthenticationService
from .credentials import CredentialService
from .daily_plans import DailyPlanService
from .manual_booking import ManualBookingService
from .db import connect_database, migrate_database
from .routes import admin, auth, availability, daily_plans, dashboard, manual_booking, profile, task_routes
from .security import (
    CredentialCipher,
    PasswordService,
    RateLimitExceeded,
    ThrottleService,
)
from .sessions import SessionService
from .settings import WebSettings
from .tasks import TaskService


PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass
class AppServices:
    connection: object
    passwords: PasswordService
    sessions: SessionService
    throttles: ThrottleService
    accounts: AccountService
    credentials: CredentialService
    tasks: TaskService | None = None
    audit: AuditService | None = None
    reauth: ReauthenticationService | None = None
    availability: AvailabilityService | None = None
    daily_plans: DailyPlanService | None = None
    manual_booking: ManualBookingService | None = None


def _default_services(settings: WebSettings) -> AppServices:
    connection = connect_database(settings.database_path)
    migrate_database(connection)
    passwords = PasswordService()
    sessions = SessionService(connection)
    throttles = ThrottleService(connection)
    accounts = AccountService(
        connection,
        passwords,
        sessions,
        throttles,
        pending_limit=settings.pending_limit,
        user_limit=settings.user_limit,
    )
    reauth = ReauthenticationService(connection, passwords, throttles)
    credentials = CredentialService(
        connection,
        CredentialCipher(settings.token_key, settings.blind_key),
        throttles,
        reauth_checker=lambda admin_id, now: reauth.is_valid(admin_id, now),
        user_limit=settings.user_limit,
    )
    return AppServices(
        connection,
        passwords,
        sessions,
        throttles,
        accounts,
        credentials,
        TaskService(connection, execution_limit=settings.daily_task_limit),
        AuditService(connection),
        reauth,
    )


def create_app(
    settings: WebSettings,
    services: AppServices | None = None,
) -> FastAPI:
    selected = services or _default_services(settings)
    migrate_database(selected.connection)
    if selected.tasks is None:
        selected.tasks = TaskService(
            selected.connection,
            execution_limit=settings.daily_task_limit,
        )
    if selected.audit is None:
        selected.audit = AuditService(selected.connection)
    if selected.reauth is None:
        selected.reauth = ReauthenticationService(
            selected.connection,
            selected.passwords,
            selected.throttles,
        )
    if selected.availability is None:
        selected.availability = AvailabilityService(
            selected.credentials, selected.throttles,
            database_path=settings.database_path,
        )
    if selected.daily_plans is None:
        selected.daily_plans = DailyPlanService(
            selected.connection, execution_limit=settings.daily_task_limit
        )
    if selected.manual_booking is None:
        selected.manual_booking = ManualBookingService(
            settings.database_path, selected.credentials
        )
    selected.credentials._reauth_checker = (
        lambda admin_id, now: selected.reauth.is_valid(admin_id, now)
    )
    app = FastAPI(title="JLU Booking", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.services = selected
    app.state.templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(_request: Request, exc: RateLimitExceeded):
        return PlainTextResponse(
            str(exc),
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(ActiveUserLimitReached)
    async def active_limit_handler(_request: Request, exc: ActiveUserLimitReached):
        return PlainTextResponse(str(exc), status_code=409)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy_handler(_request: Request, exc: sqlite3.OperationalError):
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise exc
        return PlainTextResponse("数据库暂时繁忙，请稍后重试。", status_code=503)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.cookies.get("jlu_session"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(dashboard.router)
    app.include_router(availability.router)
    app.include_router(daily_plans.router)
    app.include_router(manual_booking.router)
    app.include_router(task_routes.router)
    app.include_router(admin.router)
    return app
