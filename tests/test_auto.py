import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from jlu_booking import auto
from jlu_booking.api import ServerResponseError
from jlu_booking.config import DEFAULT_AUTO_CONFIG, save_auto_config
from jlu_booking.token_store import save_token


def test_priority_prefers_time_before_court_number():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        {
            "court_name": "羽毛球3",
            "start": "15:30",
            "end": "17:30",
        },
        {
            "court_name": "羽毛球2",
            "start": "17:30",
            "end": "19:30",
        },
    ]

    assert auto.choose_priority_slot(slots) == slots[1]


def test_priority_prefers_selected_court_within_same_time():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        {
            "court_name": "羽毛球2",
            "start": "17:30",
            "end": "19:30",
        },
        {
            "court_name": "羽毛球3",
            "start": "17:30",
            "end": "19:30",
        },
    ]

    assert auto.choose_priority_slot(slots) == slots[1]


def test_scan_phase_boundaries():
    cases = [
        ((7, 27, 59), ("waiting", None)),
        ((7, 28, 0), ("warmup", 0.3)),
        ((7, 29, 49), ("warmup", 0.3)),
        ((7, 29, 50), ("core", 0.1)),
        ((7, 33, 29), ("core", 0.1)),
        ((7, 33, 30), ("closing", 0.3)),
        ((7, 35, 59), ("closing", 0.3)),
        ((7, 36, 0), ("salvage", 10.0)),
        ((22, 29, 59), ("salvage", 10.0)),
        ((22, 30, 0), ("finished", None)),
    ]

    for (hour, minute, second), expected in cases:
        phase, interval, _ = auto.get_phase(
            datetime(2026, 1, 1, hour, minute, second)
        )
        assert (phase, interval) == expected


def _target(court_name="羽毛球3"):
    return {
        "court_name": court_name,
        "place_short_name": "ymq3",
        "start": "17:30",
        "end": "19:30",
    }


def _prepare_loop_test(monkeypatch, phases, target=None):
    settings = {
        **DEFAULT_AUTO_CONFIG,
        "companion_student_number": "example-1234",
        "real_booking_enabled": True,
    }
    auto.apply_runtime_settings(settings)
    phase_iter = iter(phases)
    sleeps = []
    monkeypatch.setattr(auto, "get_phase", lambda _now: next(phase_iter))
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 10, 7, 30).astimezone(),
    )
    monkeypatch.setattr(auto.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "log", lambda _message: None)
    monkeypatch.setattr(auto, "extract_available_slots", lambda _data: [target] if target else [])
    return sleeps


def test_warmup_only_queries_then_locks_latest_candidate(monkeypatch):
    target = _target()
    sleeps = _prepare_loop_test(
        monkeypatch,
        [
            ("warmup", 0.3, False),
            ("core", 0.1, True),
        ],
        target,
    )
    queries = []
    attempts = []
    session = object()

    def query(**kwargs):
        queries.append(kwargs)
        return {}

    def attempt(**kwargs):
        attempts.append(kwargs)
        return True

    monkeypatch.setattr(auto, "query_courts", query)
    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=session,
    )

    assert len(queries) == 1
    assert attempts[0]["slot"] == target
    assert queries[0]["session"] is session
    assert attempts[0]["session"] is session
    assert sleeps == [0.3]


def test_not_open_error_keeps_lock_and_skips_requery(monkeypatch):
    target = _target()
    sleeps = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
        target,
    )
    queries = []
    attempts = []

    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **kwargs: queries.append(kwargs) or {},
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError(
                {
                    "msg": "fail",
                    "data": "请在07:30:00至22:30:00时间内预约",
                }
            )
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 1
    assert len(attempts) == 2
    assert attempts[0]["slot"] == attempts[1]["slot"] == target
    assert sleeps == [0.1]


def test_unavailable_error_unlocks_and_requeries_immediately(monkeypatch):
    first_target = _target("羽毛球3")
    second_target = _target("羽毛球4")
    sleeps = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
        first_target,
    )
    query_targets = iter([first_target, second_target])
    queries = []
    attempts = []

    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **kwargs: queries.append(kwargs) or {},
    )
    monkeypatch.setattr(
        auto,
        "extract_available_slots",
        lambda _data: [next(query_targets)],
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "该场地已被预约"})
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 2
    assert [item["slot"] for item in attempts] == [first_target, second_target]
    assert sleeps == []


def test_closing_phase_keeps_lock_and_changes_retry_interval(monkeypatch):
    target = _target()
    sleeps = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("closing", 0.3, True),
            ("closing", 0.3, True),
        ],
        target,
    )
    queries = []
    attempts = []
    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **kwargs: queries.append(kwargs) or {},
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise ServerResponseError(
                {"msg": "fail", "data": "预约尚未开放"}
            )
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 1
    assert len(attempts) == 3
    assert sleeps == [0.1, 0.3]


def test_salvage_phase_releases_morning_lock_and_requeries(monkeypatch):
    target = _target()
    sleeps = _prepare_loop_test(
        monkeypatch,
        [
            ("closing", 0.3, True),
            ("salvage", 10.0, True),
        ],
        target,
    )
    queries = []
    attempts = []
    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **kwargs: queries.append(kwargs) or {},
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError(
                {"msg": "fail", "data": "预约尚未开放"}
            )
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 2
    assert len(attempts) == 2
    assert sleeps == [0.3]


def test_show_config_uses_readable_chinese_summary(tmp_path, capsys):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
            "real_booking_enabled": True,
        },
        config_path,
    )

    auto.main(["--config", str(config_path), "--show-config"])
    output = capsys.readouterr().out

    assert "当前自动预约配置" in output
    assert "预约场馆：前卫体育馆" in output
    assert "运动项目：羽毛球" in output
    assert "同行人：已配置（尾号 1234）" in output
    assert "运行模式：真实预约" in output
    assert "【注意】真实预约已开启" in output
    assert "example-1234" not in output


def test_show_config_json_remains_available_and_masks_companion(tmp_path, capsys):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
            "real_booking_enabled": True,
        },
        config_path,
    )

    auto.main(["--config", str(config_path), "--show-config-json"])
    output = json.loads(capsys.readouterr().out)

    assert output["companion_student_number"] == "***1234"
    assert output["real_booking_enabled"] is True


def test_auto_uses_saved_token_without_prompting(tmp_path, monkeypatch):
    token_path = tmp_path / "token"
    save_token("saved-example-token", token_path)
    monkeypatch.delenv("JLU_BOOKING_TOKEN", raising=False)
    resolve_token = auto.resolve_token
    monkeypatch.setattr(auto, "resolve_token", lambda: resolve_token(token_path))

    def fail_prompt(_prompt):
        raise AssertionError("saved Token should not prompt")

    monkeypatch.setattr(auto.getpass, "getpass", fail_prompt)

    assert auto.get_runtime_token() == ("saved-example-token", "saved")


def test_auto_prompt_saves_token_for_next_run(monkeypatch):
    saved = []
    monkeypatch.setattr(auto, "resolve_token", lambda: ("", "none"))
    monkeypatch.setattr(auto.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(auto.getpass, "getpass", lambda _prompt: "new-token")
    monkeypatch.setattr(auto, "save_token", lambda token: saved.append(token))

    assert auto.get_runtime_token() == ("new-token", "prompt_saved")
    assert saved == ["new-token"]


def test_default_auto_run_stops_before_token_when_companion_is_missing(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(DEFAULT_AUTO_CONFIG, config_path)

    def fail_token_read():
        raise AssertionError("incomplete settings must stop before reading Token")

    monkeypatch.setattr(auto, "get_runtime_token", fail_token_read)

    with pytest.raises(SystemExit) as exc_info:
        auto.main(["--config", str(config_path)])

    message = str(exc_info.value)
    assert "自动预约配置尚未完成" in message
    assert "同行人学工号为空" in message
    assert "jlu-booking" in message
    assert "--dry-run" in message


def test_explicit_dry_run_allows_missing_companion():
    auto.ensure_auto_run_ready(DEFAULT_AUTO_CONFIG, explicit_dry_run=True)


def test_daily_booking_limit_message_is_terminal():
    error = ServerResponseError(
        {
            "msg": "fail",
            "data": "当天最大预约次数：1次，剩余预约次数：0.0次",
        }
    )

    assert auto.is_daily_booking_limit_error(error) is True
    assert auto.is_daily_booking_limit_error(RuntimeError("temporary error")) is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("请在07:30:00至22:30:00时间内预约", "not_open"),
        ("该场地已被预约", "target_unavailable"),
        ("Token已失效，请重新登录", "auth"),
        ("请求过于频繁，请稍后再试", "rate_limited"),
        ("未知服务器错误", "server_rejected"),
    ],
)
def test_booking_error_classification(message, expected):
    error = ServerResponseError({"msg": "fail", "data": message})
    assert auto.classify_booking_error(error) == expected


def test_request_timing_uses_a_separate_log(tmp_path, monkeypatch):
    timing_path = tmp_path / "request_timing.log"
    monkeypatch.setattr(auto, "TIMING_LOG_FILE", timing_path)
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 10, 7, 30).astimezone(),
    )
    monotonic_values = iter([10.0, 11.582])
    monkeypatch.setattr(auto.time, "monotonic", lambda: next(monotonic_values))

    result = auto.timed_api_call(23, "query", lambda: {"ok": True})
    line = timing_path.read_text(encoding="utf-8")

    assert result == {"ok": True}
    assert "#00023" in line
    assert "query" in line
    assert "1582 ms" in line
    assert "success" in line
    assert "example-token" not in line


def test_request_timing_error_does_not_write_server_message(
    tmp_path,
    monkeypatch,
):
    timing_path = tmp_path / "request_timing.log"
    monkeypatch.setattr(auto, "TIMING_LOG_FILE", timing_path)

    def reject():
        raise ServerResponseError(
            {
                "msg": "fail",
                "data": "请在07:30:00至22:30:00时间内预约 secret-value",
            }
        )

    with pytest.raises(ServerResponseError):
        auto.timed_api_call(24, "canBook", reject)

    line = timing_path.read_text(encoding="utf-8")
    assert "error:not_open" in line
    assert "secret-value" not in line
    assert "07:30:00" not in line


def test_final_submission_transport_error_has_unknown_outcome(monkeypatch):
    target = _target()
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "can_book", lambda **_kwargs: {"msg": "success"})
    monkeypatch.setattr(
        auto,
        "book_place",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("连接已断开")),
    )

    with pytest.raises(auto.BookingOutcomeUnknown):
        auto.attempt_real_booking(
            query_date="2026-09-11",
            slot=target,
            companion_id=123,
            companion_name="示例用户",
            token="example-token",
            session=object(),
            request_id=1,
        )


def test_existing_success_state_stops_before_token_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(DEFAULT_AUTO_CONFIG, config_path)
    success_path = tmp_path / "success.json"
    monkeypatch.setattr(
        auto,
        "existing_success_state_path",
        lambda _date: success_path,
    )
    monkeypatch.setattr(
        auto,
        "get_runtime_token",
        lambda: (_ for _ in ()).throw(
            AssertionError("success state must stop before Token lookup")
        ),
    )

    auto.main(["--config", str(config_path)])
    output = capsys.readouterr().out

    assert "本次直接退出" in output
    assert str(success_path) in output


def test_real_booking_stops_after_daily_limit_response(
    tmp_path,
    monkeypatch,
    capsys,
):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
            "real_booking_enabled": True,
        },
        config_path,
    )
    target = {
        "court_name": "乒乓球3",
        "place_short_name": "ppq3",
        "start": "17:30",
        "end": "19:30",
    }
    attempts = []

    monkeypatch.setenv("JLU_BOOKING_TOKEN", "example-token")
    monkeypatch.setattr(auto, "existing_success_state_path", lambda _date: None)
    monkeypatch.setattr(
        auto,
        "get_companion_user",
        lambda **_kwargs: {"id": 123, "name": "示例用户"},
    )
    monkeypatch.setattr(auto, "wait_until_start", lambda: None)
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 9, 11, 29, 19).astimezone(),
    )
    monkeypatch.setattr(auto, "query_courts", lambda **_kwargs: {})
    monkeypatch.setattr(auto, "extract_available_slots", lambda _data: [target])
    monkeypatch.setattr(auto, "log", lambda message: None)
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)

    def reject_for_daily_limit(**_kwargs):
        attempts.append(True)
        raise ServerResponseError(
            {
                "msg": "fail",
                "data": "当天最大预约次数：1次，剩余预约次数：0.0次",
            }
        )

    monkeypatch.setattr(auto, "attempt_real_booking", reject_for_daily_limit)

    auto.main(["--config", str(config_path)])
    output = capsys.readouterr().out

    assert len(attempts) == 1
    assert "检测到当天预约次数已用完，自动任务已停止" in output
    assert "程序不会继续扫描或重复提交" in output
    assert "预约尝试失败，继续扫描" not in output
