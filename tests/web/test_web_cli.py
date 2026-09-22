import base64
import os

import pytest

from jlu_booking.web import cli
from jlu_booking.web.db import connect_database


def test_generate_keys_writes_private_files_without_printing_key_bytes(
    tmp_path, capsys
):
    directory = tmp_path / "secrets"

    assert cli.main(["generate-keys", "--directory", str(directory)]) == 0

    token_key = directory / "token.key"
    blind_key = directory / "blind.key"
    assert token_key.is_file()
    assert blind_key.is_file()
    if os.name != "nt":
        assert token_key.stat().st_mode & 0o777 == 0o600
        assert blind_key.stat().st_mode & 0o777 == 0o600
    assert len(base64.urlsafe_b64decode(token_key.read_bytes().strip())) == 32
    assert len(base64.urlsafe_b64decode(blind_key.read_bytes().strip())) == 32
    output = capsys.readouterr().out
    assert str(token_key) in output
    assert str(blind_key) in output
    assert token_key.read_text(encoding="ascii").strip() not in output
    assert blind_key.read_text(encoding="ascii").strip() not in output


def test_generate_keys_refuses_overwrite_without_changing_existing_file(tmp_path):
    directory = tmp_path / "secrets"
    cli.main(["generate-keys", "--directory", str(directory)])
    original_token = (directory / "token.key").read_bytes()
    original_blind = (directory / "blind.key").read_bytes()

    with pytest.raises(SystemExit):
        cli.main(["generate-keys", "--directory", str(directory)])

    assert (directory / "token.key").read_bytes() == original_token
    assert (directory / "blind.key").read_bytes() == original_blind


def test_create_admin_prompts_twice_and_never_prints_password(
    tmp_path, capsys
):
    secrets_dir = tmp_path / "secrets"
    cli.main(["generate-keys", "--directory", str(secrets_dir)])
    capsys.readouterr()
    prompts = []

    def read_password(prompt):
        prompts.append(prompt)
        return "correct horse battery staple"

    environ = {
        "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
        "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(secrets_dir / "token.key"),
        "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(secrets_dir / "blind.key"),
    }

    assert cli.main(
        ["create-admin", "Owner"],
        environ=environ,
        password_reader=read_password,
    ) == 0

    assert len(prompts) == 2
    output = capsys.readouterr().out
    assert "owner" in output
    assert "correct horse battery staple" not in output
    connection = connect_database(tmp_path / "data" / "web.sqlite3")
    row = connection.execute(
        "SELECT username, role, status, password_hash FROM users"
    ).fetchone()
    assert tuple(row[:3]) == ("owner", "admin", "active")
    assert "correct horse battery staple" not in row["password_hash"]


def test_create_admin_rejects_password_mismatch(tmp_path):
    secrets_dir = tmp_path / "secrets"
    cli.main(["generate-keys", "--directory", str(secrets_dir)])
    answers = iter(["correct horse battery staple", "different password value"])
    environ = {
        "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
        "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(secrets_dir / "token.key"),
        "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(secrets_dir / "blind.key"),
    }

    with pytest.raises(SystemExit, match="两次输入"):
        cli.main(
            ["create-admin", "owner"],
            environ=environ,
            password_reader=lambda _prompt: next(answers),
        )


def test_backup_command_prints_only_created_path(monkeypatch, capsys):
    expected = "/safe/backups/2026-09-22.sqlite3"
    monkeypatch.setattr(cli, "_run_backup", lambda *, environ: expected)
    assert cli.main(["backup"], environ={}) == 0
    assert capsys.readouterr().out.strip() == expected


def test_scheduler_command_dispatches_without_printing_secrets(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_run_scheduler", lambda *, environ: calls.append(environ) or 0)
    environment = {"PRIVATE": "must-not-print"}
    assert cli.main(["scheduler"], environ=environment) == 0
    assert calls == [environment]
    assert "must-not-print" not in capsys.readouterr().out
