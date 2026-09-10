import json

import pytest

from jlu_booking.config import (
    DEFAULT_AUTO_CONFIG,
    load_auto_config,
    require_companion_student_number,
    normalize_time_priority,
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
