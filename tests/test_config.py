import json
import os
import stat

import pytest

from jlu_booking.config import (
    DEFAULT_AUTO_CONFIG,
    load_auto_config,
    require_companion_student_number,
    normalize_time_priority,
    save_auto_config,
    validate_auto_config,
)
from jlu_booking.paths import default_config_file, default_runtime_dir


def test_missing_config_is_created_with_safe_defaults(tmp_path):
    config_path = tmp_path / "auto_booking.json"

    config = load_auto_config(config_path)

    assert config_path.exists()
    assert config["venue"] == "前卫体育馆"
    assert config["sport"] == "羽毛球"
    assert config["real_booking_enabled"] is False
    assert config["companion_student_number"] == ""
    assert json.loads(config_path.read_text(encoding="utf-8")) == config


def test_time_priority_is_deduplicated_without_reordering():
    assert normalize_time_priority(
        [["17:30", "19:30"], ["10:00", "12:00"], ["17:30", "19:30"]]
    ) == [["17:30", "19:30"], ["10:00", "12:00"]]


@pytest.mark.parametrize(
    "value",
    [
        [],
        [["25:00", "26:00"]],
        [["19:30", "17:30"]],
        ["17:30-19:30"],
    ],
)
def test_invalid_time_priority_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_time_priority(value)


def test_real_booking_requires_companion():
    config = {**DEFAULT_AUTO_CONFIG, "real_booking_enabled": True}

    with pytest.raises(ValueError, match="同行人学工号"):
        validate_auto_config(config)


def test_save_persists_companion_student_number_for_next_launch(tmp_path):
    config_path = tmp_path / "auto_booking.json"

    runtime = save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
            "real_booking_enabled": True,
        },
        config_path,
    )

    assert runtime["companion_student_number"] == "example-1234"
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["companion_student_number"] == "example-1234"
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_load_remembers_companion_student_number(tmp_path):
    config_path = tmp_path / "auto_booking.json"
    legacy = {
        **DEFAULT_AUTO_CONFIG,
        "companion_student_number": "legacy-1234",
        "real_booking_enabled": True,
    }
    config_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = load_auto_config(config_path)

    assert loaded["companion_student_number"] == "legacy-1234"
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["companion_student_number"] == "legacy-1234"
    assert persisted["real_booking_enabled"] is True


def test_gui_saved_plan_requires_a_companion_number():
    assert require_companion_student_number(" 20260001 ") == "20260001"
    with pytest.raises(ValueError, match="必填项"):
        require_companion_student_number("   ")


def test_path_environment_overrides(monkeypatch, tmp_path):
    config_path = tmp_path / "custom.json"
    runtime_path = tmp_path / "custom-runtime"
    monkeypatch.setenv("JLU_BOOKING_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("JLU_BOOKING_RUNTIME_DIR", str(runtime_path))

    assert default_config_file() == config_path
    assert default_runtime_dir() == runtime_path
