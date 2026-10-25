"""The desktop shell reuses the Web workspace without Web-only modules."""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jlu_booking.local.bootstrap import create_local_app, ensure_local_settings
from jlu_booking.web.routes.profile import _token_input


TOKEN = "0123456789abcdef0123456789abcdef0"
IDENTITY = "0123456789abcdef0123456789abcdef"


def _local_app(tmp_path: Path, *, token: str = TOKEN, companion: str = "20240001"):
    return create_local_app(
        base_dir=tmp_path / "local",
        token=token,
        companion_number=companion,
    )


def test_local_workspace_exposes_three_destinations(tmp_path):
    app, _settings, identity = _local_app(tmp_path)
    assert identity.status == "active"
    assert identity.has_token is True

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        html = home.text
        assert html.count("workspace-nav-card") == 3
        assert "3 项" in html
        assert "场地查询" in html
        assert "启动预约" in html
        assert "个人中心" in html
        assert "自动预约" not in html
        assert "daily-plan" not in html
        assert 'action="/logout"' not in html
        assert "本机用户" in html

        task_form = client.get("/tasks/new")
        assert task_form.status_code == 200
        assert "立即启动预约" in task_form.text
        assert "自动预约" not in task_form.text

        profile = client.get("/profile")
        assert profile.status_code == 200
        assert "本机单用户" in profile.text
        assert "修改登录密码" not in profile.text
        assert "加密保存在本机" in profile.text


def test_web_only_routes_are_absent(tmp_path):
    app, _settings, _identity = _local_app(tmp_path)
    with TestClient(app) as client:
        for path in ("/login", "/register", "/daily-plan", "/admin"):
            assert client.get(path, follow_redirects=False).status_code == 404, path


def test_missing_companion_is_explained_on_the_query_page(tmp_path):
    app, _settings, identity = _local_app(tmp_path, token=TOKEN, companion="")
    assert identity.has_companion is False
    with TestClient(app) as client:
        html = client.get("/").text
        assert "验证同行人学工号" in html
        assert 'href="/profile"' in html


def test_first_run_requires_a_token_once(tmp_path):
    app, _settings, identity = _local_app(tmp_path, token="", companion="")
    assert identity.status == "pending_token"
    assert identity.has_token is False

    with TestClient(app) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 303
        assert home.headers["location"] == "/onboarding/token"

        onboarding = client.get("/onboarding/token")
        assert onboarding.status_code == 200
        assert "首次使用" in onboarding.text
        assert "加密保存在本机" in onboarding.text
        assert "退出登录" not in onboarding.text


def test_local_token_and_companion_are_imported_into_private_storage(tmp_path):
    base = tmp_path / "local"
    app, settings, identity = _local_app(tmp_path)
    assert identity.imported_token is True
    assert identity.has_companion is True

    with TestClient(app) as client:
        profile = client.get("/profile")
        assert profile.status_code == 200
        # The full token never appears in the page.
        assert TOKEN not in profile.text
        assert "已保存同行人" in profile.text

    assert settings.database_path.is_file()
    if os.name != "nt":
        assert (base / "secrets" / "token.key").stat().st_mode & 0o077 == 0
        assert (base / "secrets" / "blind.key").stat().st_mode & 0o077 == 0


def test_settings_are_stable_across_restarts(tmp_path):
    first = ensure_local_settings(tmp_path / "local")
    second = ensure_local_settings(tmp_path / "local")
    assert first.token_key == second.token_key
    assert first.blind_key == second.blind_key


def test_navigation_count_is_only_changed_for_local_mode(tmp_path):
    app, _settings, _identity = _local_app(tmp_path)
    with TestClient(app) as client:
        html = client.get("/").text
        nav = re.search(r"<nav aria-label=\"用户导航\">(.*?)</nav>", html, re.S)
        assert nav is not None
        assert "自动预约" not in nav.group(1)


def _request(local_mode):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(local_mode=local_mode))
    )


def test_local_token_input_accepts_identity_url():
    url = (
        "https://ss.jlu.edu.cn/easyserpApp/index.html#/branch"
        f"?clubId=26&userId={IDENTITY}"
    )
    assert _token_input(_request(True), url) == IDENTITY
    assert _token_input(_request(True), IDENTITY) == IDENTITY


def test_local_token_input_rejects_broken_identity_url():
    bad = (
        "https://ss.jlu.edu.cn/easyserpApp/index.html#/branch"
        "?clubId=99&userId=" + IDENTITY
    )
    with pytest.raises(ValueError):
        _token_input(_request(True), bad)


def test_web_mode_keeps_raw_token_contract():
    assert _token_input(_request(False), "raw-token") == "raw-token"
