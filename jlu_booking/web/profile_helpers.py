"""Shared redirect guard for active-user Web pages."""

from fastapi.responses import RedirectResponse

from .dependencies import current_user


def require_active_user(request):
    session, user = current_user(request)
    if session is None or user is None:
        return None, None, RedirectResponse("/login", 303)
    if user.must_change_password:
        return None, None, RedirectResponse("/change-password", 303)
    if user.status == "pending_token":
        return None, None, RedirectResponse("/onboarding/token", 303)
    return session, user, None
