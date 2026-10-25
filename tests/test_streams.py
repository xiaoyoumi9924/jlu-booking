"""Windowed Windows builds have no stdout/stderr; startup must survive that."""

from __future__ import annotations

import io
import sys

from jlu_booking.local import __main__ as local_main
from jlu_booking.streams import NullStream, ensure_standard_streams


class BrokenStream:
    encoding = "utf-8"

    def write(self, _text):
        raise OSError("bad file descriptor")

    def flush(self):
        raise OSError("bad file descriptor")


def test_missing_streams_are_replaced_with_safe_stand_ins(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_standard_streams()

    assert isinstance(sys.stdout, NullStream)
    assert isinstance(sys.stderr, NullStream)
    assert sys.stdout.write("测试") == 2
    sys.stdout.flush()


def test_broken_streams_are_replaced(monkeypatch):
    monkeypatch.setattr(sys, "stdout", BrokenStream())
    monkeypatch.setattr(sys, "stderr", BrokenStream())

    ensure_standard_streams()

    assert isinstance(sys.stdout, NullStream)
    assert isinstance(sys.stderr, NullStream)


def test_usable_streams_are_kept(monkeypatch):
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    ensure_standard_streams()

    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_local_entrypoint_survives_a_console_free_launch(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(local_main, "_configure_logging", lambda: None)
    monkeypatch.setattr(local_main, "_run", lambda _args: 0)

    assert local_main.main(["--no-window"]) == 0
    assert isinstance(sys.stdout, NullStream)
    assert isinstance(sys.stderr, NullStream)


def test_startup_failure_is_written_for_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(local_main, "local_data_dir", lambda: tmp_path)
    monkeypatch.setattr(local_main, "_show_error_dialog", lambda _message: None)

    try:
        raise RuntimeError("probe failure")
    except RuntimeError as exc:
        local_main._report_startup_failure(exc)

    text = (tmp_path / "startup-error.log").read_text(encoding="utf-8")
    assert "RuntimeError: probe failure" in text
