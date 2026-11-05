"""The single-user Web deployment: no login, but automatic booking stays."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jlu_booking.local.bootstrap import create_local_web_app, ensure_local_settings


TOKEN = "0123456789abcdef0123456789abcdef0"
COMPANION = "20240001"


def _app(tmp_path: Path):
    settings = ensure_local_settings(tmp_path / "data")
    return create_local_web_app(
        settings, token=TOKEN, companion_number=COMPANION
    )


def test_local_web_has_no_login_registration_or_admin(tmp_path):
    app, identity = _app(tmp_path)

    assert identity.status == "active"
    assert app.state.local_session is not None
    assert app.state.local_auto is True

    with TestClient(app) as client:
        assert client.get("/login").status_code == 404
        assert client.get("/register").status_code == 404
        assert client.get("/admin").status_code == 404


def test_local_web_opens_signed_in_with_four_destinations(tmp_path):
    app, _identity = _app(tmp_path)

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert home.text.count("workspace-nav-card") == 4
        assert "自动预约" in home.text
        assert "本机用户" in home.text
        assert client.get("/profile").status_code == 200


def test_local_web_keeps_the_daily_plan_page(tmp_path):
    app, _identity = _app(tmp_path)

    with TestClient(app) as client:
        page = client.get("/daily-plan")
        assert page.status_code == 200
        assert "每日预约方案" in page.text
        assert "开启每日自动预约" in page.text
