"""FastAPI application factory for the optional browser interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .accounts import AccountService
from .credentials import CredentialService
from .db import connect_database, migrate_database
from .routes import auth, profile
from .security import CredentialCipher, PasswordService, ThrottleService
from .sessions import SessionService
from .settings import WebSettings


PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass
class AppServices:
    connection: object
    passwords: PasswordService
    sessions: SessionService
    throttles: ThrottleService
    accounts: AccountService
    credentials: CredentialService


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
    credentials = CredentialService(
        connection,
        CredentialCipher(settings.token_key, settings.blind_key),
        throttles,
        user_limit=settings.user_limit,
    )
    return AppServices(connection, passwords, sessions, throttles, accounts, credentials)


def create_app(
    settings: WebSettings,
    services: AppServices | None = None,
) -> FastAPI:
    selected = services or _default_services(settings)
    migrate_database(selected.connection)
    app = FastAPI(title="JLU Booking", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.services = selected
    app.state.templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

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
    return app

