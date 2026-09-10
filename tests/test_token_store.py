import os
import stat

import pytest

from jlu_booking import token_cli, token_store
from jlu_booking.gui import BookingApp


def test_saved_token_round_trip_and_permissions(tmp_path):
    token_path = tmp_path / "private" / "token"

    saved_path = token_store.save_token("example-token", token_path)

    assert saved_path == token_path
    assert token_store.load_saved_token(token_path) == "example-token"
    if os.name != "nt":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_resolve_token_prefers_environment_over_saved_token(tmp_path):
    token_path = tmp_path / "token"
    token_store.save_token("saved-token", token_path)

    assert token_store.resolve_token(
        token_path,
        {"JLU_BOOKING_TOKEN": "environment-token"},
    ) == ("environment-token", "environment")
    assert token_store.resolve_token(token_path, {}) == ("saved-token", "saved")


def test_missing_saved_token_has_no_active_source(tmp_path):
    assert token_store.resolve_token(tmp_path / "missing", {}) == ("", "none")


def test_empty_token_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="不能为空"):
        token_store.save_token("   ", tmp_path / "token")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("raw-token", "raw-token"),
        (
            "https://example.edu/easyserpClient?shopNum=1&token=url-token&x=2",
            "url-token",
        ),
        ("https://example.edu/path?token=literal+plus", "literal+plus"),
        ("shopNum=1&token=query%2Btoken&x=2", "query+token"),
    ],
)
def test_extract_token_input_accepts_raw_token_or_request_url(value, expected):
    assert token_store.extract_token_input(value) == expected


def test_extract_token_input_rejects_empty_token_parameter():
    with pytest.raises(ValueError, match="不能为空"):
        token_store.extract_token_input("https://example.edu/path?token=")


def test_clear_saved_token(tmp_path):
    token_path = tmp_path / "token"
    token_store.save_token("example-token", token_path)

    assert token_store.clear_saved_token(token_path) is True
    assert token_store.clear_saved_token(token_path) is False


def test_token_cli_set_updates_without_printing_value(tmp_path, monkeypatch, capsys):
    token_path = tmp_path / "token"
    tokens = iter(["first-secret-token", "second-secret-token"])
    monkeypatch.setattr(token_cli.getpass, "getpass", lambda _prompt: next(tokens))

    token_cli.main(["set"], token_path=token_path)
    token_cli.main(["set"], token_path=token_path)
    output = capsys.readouterr().out

    assert token_store.load_saved_token(token_path) == "second-secret-token"
    assert "first-secret-token" not in output
    assert "second-secret-token" not in output
    assert "Token 已保存或更新" in output


def test_token_cli_status_never_displays_token(tmp_path, monkeypatch, capsys):
    token_path = tmp_path / "token"
    token_store.save_token("never-print-this-token", token_path)
    monkeypatch.delenv("JLU_BOOKING_TOKEN", raising=False)

    token_cli.main(["status"], token_path=token_path)
    output = capsys.readouterr().out

    assert "本机保存状态：已保存" in output
    assert "never-print-this-token" not in output


def test_gui_prompt_saves_token_for_later_tasks(monkeypatch):
    app = BookingApp.__new__(BookingApp)
    app.root = object()
    app.token = ""
    app.token_source = "none"
    app.token_validated = False
    saved = []
    validated = []

    monkeypatch.setattr(
        "jlu_booking.gui.simpledialog.askstring",
        lambda *_args, **_kwargs: "gui-token",
    )
    monkeypatch.setattr("jlu_booking.gui.save_token", lambda token: saved.append(token))
    app.validate_token = lambda token: validated.append(token)

    assert app.get_token() == "gui-token"
    assert saved == ["gui-token"]
    assert validated == ["gui-token"]
    assert app.token_validated is True
    assert app.token_source == "saved"


def test_gui_reprompts_until_token_passes_server_validation(monkeypatch):
    app = BookingApp.__new__(BookingApp)
    app.root = object()
    app.token = ""
    app.token_source = "none"
    app.token_validated = False
    entered = iter(["expired-token", "valid-token"])
    saved = []
    errors = []

    monkeypatch.setattr(
        "jlu_booking.gui.simpledialog.askstring",
        lambda *_args, **_kwargs: next(entered),
    )
    monkeypatch.setattr(
        "jlu_booking.gui.messagebox.showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    monkeypatch.setattr("jlu_booking.gui.save_token", lambda token: saved.append(token))

    def validate(token):
        if token == "expired-token":
            raise RuntimeError("Token已失效")

    app.validate_token = validate

    assert app.get_token() == "valid-token"
    assert saved == ["valid-token"]
    assert len(errors) == 1
    assert errors[0][0] == "Token 验证未通过"
    assert "expired-token" not in errors[0][1]
