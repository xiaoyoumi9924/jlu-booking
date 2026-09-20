import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from jlu_booking import auto
from jlu_booking.api import ServerResponseError
from jlu_booking.config import DEFAULT_AUTO_CONFIG, save_auto_config


def _slot(court_name, place_short_name, start, end):
    return {
        "court_name": court_name,
        "place_short_name": place_short_name,
        "start": start,
        "end": end,
    }


def test_priority_prefers_time_before_court_number():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        _slot("羽毛球3", "ymq3", "15:30", "17:30"),
        _slot("羽毛球2", "ymq2", "17:30", "19:30"),
    ]

    assert auto.choose_priority_slot(slots) == slots[1]


def test_priority_prefers_selected_court_within_same_time():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        _slot("羽毛球2", "ymq2", "17:30", "19:30"),
        _slot("羽毛球3", "ymq3", "17:30", "19:30"),
    ]

    assert auto.choose_priority_slot(slots) == slots[1]


def test_candidates_sort_known_times_before_unknown_times():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        _slot("羽毛球1", "ymq1", "08:00", "09:00"),
        _slot("羽毛球4", "ymq4", "06:00", "07:30"),
        _slot("羽毛球2", "ymq2", "05:00", "06:00"),
    ]

    assert auto.sort_booking_candidates(slots) == [
        slots[1],
        slots[2],
        slots[0],
    ]


def test_unknown_times_are_sorted_chronologically():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    slots = [
        _slot("羽毛球2", "ymq2", "12:30", "13:00"),
        _slot("羽毛球1", "ymq1", "05:00", "06:00"),
    ]

    assert auto.sort_booking_candidates(slots) == [slots[1], slots[0]]


def test_preferred_court_wins_then_other_courts_sort_by_number():
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    preferred = _slot("羽毛球3", "ymq3", "17:30", "19:30")
    court_five = _slot("羽毛球5", "ymq5", "17:30", "19:30")
    court_one = _slot("羽毛球1", "ymq1", "17:30", "19:30")

    assert auto.sort_booking_candidates(
        [court_five, preferred, court_one]
    ) == [preferred, court_one, court_five]


def test_candidate_key_requires_a_complete_submission_identity():
    assert auto.candidate_key(
        _slot("羽毛球3", "ymq3", "17:30", "19:30")
    ) == ("ymq3", "17:30", "19:30")
    assert auto.candidate_key(
        {"court_name": "羽毛球3", "start": "17:30", "end": "19:30"}
    ) is None


def test_scan_phase_boundaries():
    cases = [
        ((7, 27, 59), ("waiting", None)),
        ((7, 28, 0), ("warmup", 0.3)),
        ((7, 29, 54), ("warmup", 0.3)),
        ((7, 29, 55), ("core", 0.1)),
        ((7, 32, 59), ("core", 0.1)),
        ((7, 33, 0), ("closing", 0.3)),
        ((7, 35, 59), ("closing", 0.3)),
        # 全天捡漏已关闭：收尾结束后直接进入当天结束。
        ((7, 36, 0), ("finished", None)),
        ((22, 29, 59), ("finished", None)),
        ((22, 30, 0), ("finished", None)),
    ]

    for (hour, minute, second), expected in cases:
        phase, interval, _ = auto.get_phase(
            datetime(2026, 1, 1, hour, minute, second)
        )
        assert (phase, interval) == expected


def test_no_salvage_phase_at_any_time_of_day():
    """全天捡漏已关闭：一天中任何时刻都不应再出现 salvage 阶段。"""

    seen = set()
    for hour in range(24):
        for minute in range(60):
            phase, _, _ = auto.get_phase(datetime(2026, 1, 1, hour, minute, 0))
            seen.add(phase)

    assert "salvage" not in seen
    assert seen == {"waiting", "warmup", "core", "closing", "finished"}

    # 07:36 之后不再有任何允许提交预约的阶段。
    for hour in range(7, 24):
        for minute in range(60):
            if (hour, minute) < (7, 36):
                continue
            phase, interval, allow_booking = auto.get_phase(
                datetime(2026, 1, 1, hour, minute, 0)
            )
            assert (phase, interval, allow_booking) == ("finished", None, False)


def _target(court_name="羽毛球3", place_short_name=None):
    if place_short_name is None:
        place_short_name = f"ymq{court_name[-1]}"
    return _slot(court_name, place_short_name, "17:30", "19:30")


def _prepare_loop_test(monkeypatch, phases):
    settings = {
        **DEFAULT_AUTO_CONFIG,
        "companion_student_number": "example-1234",
        "real_booking_enabled": True,
    }
    auto.apply_runtime_settings(settings)
    phase_iter = iter(phases)
    sleeps = []
    event_logs = []
    monkeypatch.setattr(auto, "get_phase", lambda _now: next(phase_iter))
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 10, 7, 30).astimezone(),
    )
    monkeypatch.setattr(auto.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "log", event_logs.append)
    monkeypatch.setattr(auto, "extract_available_slots", lambda data: data)
    return sleeps, event_logs


def _install_query_outcomes(monkeypatch, outcomes, queries):
    outcome_iter = iter(outcomes)

    def query(**kwargs):
        queries.append(kwargs)
        outcome = next(outcome_iter)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(auto, "query_courts", query)


def test_core_discards_warmup_candidate_and_queries_fresh_state(monkeypatch):
    warm_target = _target("羽毛球3")
    fresh_target = _target("羽毛球4")
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [
            ("warmup", 0.3, False),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    session = object()
    _install_query_outcomes(
        monkeypatch,
        [[warm_target], [fresh_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=session,
    )

    assert len(queries) == 2
    assert attempts[0]["slot"] == fresh_target
    assert queries[0]["session"] is session
    assert attempts[0]["session"] is session
    assert sleeps == [0.3]


def test_not_open_error_keeps_lock_and_skips_requery(monkeypatch):
    target = _target()
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[target]], queries)

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
    assert any("PREOPEN | not_open" in line for line in event_logs)
    assert not any("TARGET_FAILED" in line for line in event_logs)


def test_first_explicit_failure_detects_open_and_requeries_immediately(
    monkeypatch,
):
    first_target = _target("羽毛球3")
    second_target = _target("羽毛球4")
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [[first_target, second_target], [first_target, second_target]],
        queries,
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
    assert any("OPEN_DETECTED | result=target_unavailable" in line for line in event_logs)
    assert any("TARGET_FAILED | round=1" in line for line in event_logs)


def test_closing_phase_keeps_lock_and_changes_retry_interval(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("closing", 0.3, True),
            ("closing", 0.3, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[target]], queries)

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


def test_finished_phase_ends_the_day_without_salvage(monkeypatch):
    """全天捡漏已关闭：收尾阶段结束后当天任务立即结束，不再继续扫描。"""

    morning_target = _target("羽毛球3")
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [
            ("closing", 0.3, True),
            ("finished", None, False),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[morning_target]], queries)

    def attempt(**kwargs):
        attempts.append(kwargs)
        raise ServerResponseError(
            {"msg": "fail", "data": "预约尚未开放"}
        )

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 1
    assert [item["slot"] for item in attempts] == [morning_target]
    assert sleeps == [0.3]
    assert "当天停止时间到达 | 未预约成功" in "\n".join(event_logs)


def test_closing_phase_can_still_book(monkeypatch):
    """收尾阶段（07:33-07:36）仍然允许真实提交，关闭捡漏不影响它。"""

    target = _target("羽毛球3")
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("closing", 0.3, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[target]], queries)

    def attempt(**kwargs):
        attempts.append(kwargs)
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
    assert [item["slot"] for item in attempts] == [target]
    assert sleeps == []


def test_new_candidate_can_join_the_current_round(monkeypatch):
    first_target = _target("羽毛球3")
    new_target = _target("羽毛球5")
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [[first_target], [first_target, new_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError(
                {"msg": "fail", "data": "当前时间段宝地已有用户预约"}
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

    assert [item["slot"] for item in attempts] == [first_target, new_target]
    assert sleeps == []
    assert sum("ROUND_START | round=1" in line for line in event_logs) == 1


def test_round_exhaustion_waits_then_allows_failed_candidate_again(monkeypatch):
    target = _target()
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[target], [target], [target]], queries)

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

    assert [item["slot"] for item in attempts] == [target, target]
    assert len(queries) == 3
    assert sleeps == [0.1]
    assert any("ROUND_END | round=1 | attempted=1" in line for line in event_logs)
    assert any("ROUND_START | round=2" in line for line in event_logs)


def test_open_detected_never_reverts_after_a_late_not_open(monkeypatch):
    first_target = _target("羽毛球3")
    next_target = _target("羽毛球4")
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [[first_target], [first_target, next_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "该场地已被预约"})
        if len(attempts) == 2:
            raise ServerResponseError({"msg": "fail", "data": "预约尚未开放"})
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert [item["slot"] for item in attempts] == [
        first_target,
        next_target,
        next_target,
    ]
    assert len(queries) == 2
    assert sleeps == [0.1]
    assert sum("OPEN_DETECTED" in line for line in event_logs) == 1


def test_query_transport_error_preserves_failed_candidates_in_round(monkeypatch):
    first_target = _target("羽毛球3")
    second_target = _target("羽毛球4")
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [
            [first_target, second_target],
            requests.ConnectionError("query disconnected"),
            [first_target, second_target],
        ],
        queries,
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

    assert [item["slot"] for item in attempts] == [first_target, second_target]
    assert sleeps == [0.1]


def test_rate_limit_and_precheck_transport_keep_the_locked_target(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [
            ("core", 0.1, True),
            ("core", 0.1, True),
            ("core", 0.1, True),
        ],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[target]], queries)

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "请求过于频繁"})
        if len(attempts) == 2:
            raise requests.ConnectionError("canBook disconnected")
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
    assert [item["slot"] for item in attempts] == [target, target, target]
    assert sleeps == [5.0, 0.1]


def test_dynamic_logs_include_counts_without_sensitive_values(monkeypatch):
    first_target = _target("羽毛球3")
    second_target = _target("羽毛球5")
    invalid_target = {
        "court_name": "羽毛球4",
        "start": "17:30",
        "end": "19:30",
    }
    _, event_logs = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    _install_query_outcomes(
        monkeypatch,
        [[first_target], [first_target, second_target, invalid_target]],
        queries,
    )
    attempts = []

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            # 第一个候选明确失败即视为开放，进入动态候选轮次。
            raise ServerResponseError(
                {"msg": "fail", "data": "当前时间段宝地已有用户预约"}
            )
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="private-student-name",
        token="private-example-token",
        session=object(),
    )

    combined = "\n".join(event_logs)
    assert "OPEN_DETECTED | result=target_unavailable" in combined
    assert "ROUND_START | round=1" in combined
    assert "QUERY | round=1 | visible=3 | eligible=1 | skipped=2" in combined
    assert "TRY | round=1 | attempt=1" in combined
    assert "private-example-token" not in combined
    assert "private-student-name" not in combined


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

    auto.main(
        [
            "--config",
            str(config_path),
            "--companion",
            "example-1234",
            "--show-config",
        ]
    )
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

    auto.main(
        [
            "--config",
            str(config_path),
            "--companion",
            "example-1234",
            "--show-config-json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert output["companion_student_number"] == "***1234"
    assert output["real_booking_enabled"] is True


def test_auto_reuses_saved_token_without_prompting(tmp_path, monkeypatch):
    token_path = tmp_path / "token"
    token_path.write_text("saved-example-token\n", encoding="utf-8")
    monkeypatch.delenv("JLU_BOOKING_TOKEN", raising=False)
    resolve_token = auto.resolve_token
    monkeypatch.setattr(auto, "resolve_token", lambda: resolve_token(token_path))
    monkeypatch.setattr(auto.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        auto.getpass,
        "getpass",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("已有保存值时不应再次询问 Token")
        ),
    )

    assert auto.get_runtime_token() == ("saved-example-token", "saved")
    assert token_path.exists()


def test_auto_prompt_saves_token_for_future_runs(monkeypatch):
    monkeypatch.setattr(auto, "resolve_token", lambda: ("", "none"))
    monkeypatch.setattr(auto.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(auto.getpass, "getpass", lambda _prompt: "new-token")
    saved = []
    monkeypatch.setattr(auto, "save_token", lambda token: saved.append(token))

    assert auto.get_runtime_token() == ("new-token", "prompt_saved")
    assert saved == ["new-token"]


def test_companion_can_be_supplied_by_process_environment(tmp_path):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(DEFAULT_AUTO_CONFIG, config_path)
    args = auto.build_arg_parser().parse_args(["--config", str(config_path)])

    settings, _, has_overrides = auto.load_runtime_settings(
        args,
        {"JLU_BOOKING_COMPANION": " session-1234 "},
    )

    assert settings["companion_student_number"] == "session-1234"
    assert has_overrides is True


def test_auto_source_contains_only_gbk_encodable_characters():
    source = Path(auto.__file__).read_text(encoding="utf-8")

    source.encode("gbk")


def test_success_state_contains_no_identity_or_server_payload(tmp_path, monkeypatch):
    auto.apply_runtime_settings(DEFAULT_AUTO_CONFIG)
    monkeypatch.setattr(auto, "STATE_DIR", tmp_path)

    auto.save_success_state("2026-09-11", _target())

    payload = json.loads(auto.success_state_path("2026-09-11").read_text("utf-8"))
    assert "companion_name" not in payload
    assert "server_result" not in payload
    assert payload["court_name"] == "羽毛球3"


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


@pytest.mark.parametrize(
    "message",
    [
        "当前时间段宝地已有用户预约",
        "下手太晚了，该场地已被其他用户预约",
        "该时段不可预约，请重新选择场地",
    ],
)
def test_new_occupied_messages_are_target_unavailable(message):
    error = ServerResponseError({"msg": "fail", "data": message})

    assert auto.classify_booking_error(error) == "target_unavailable"


def test_please_reselect_alone_is_not_target_unavailable():
    error = ServerResponseError(
        {"msg": "fail", "data": "系统繁忙，请重新选择"}
    )

    assert auto.classify_booking_error(error) == "rate_limited"


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
    monkeypatch.setenv("JLU_BOOKING_COMPANION", "example-1234")
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
        # 必须落在新的核心抢票阶段内，否则全天捡漏关闭后任务会直接结束。
        lambda: datetime(2026, 9, 9, 7, 32, 0).astimezone(),
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


def test_late_start_after_finish_exits_without_scanning(
    tmp_path,
    monkeypatch,
    capsys,
):
    """07:36 之后启动（旧版会进入全天捡漏）：现在应直接结束，不发任何请求。"""

    config_path = tmp_path / "auto_booking.json"
    save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
            "real_booking_enabled": True,
        },
        config_path,
    )
    queries = []
    attempts = []

    monkeypatch.setenv("JLU_BOOKING_TOKEN", "example-token")
    monkeypatch.setenv("JLU_BOOKING_COMPANION", "example-1234")
    monkeypatch.setattr(auto, "existing_success_state_path", lambda _date: None)
    monkeypatch.setattr(
        auto,
        "get_companion_user",
        lambda **_kwargs: {"id": 123, "name": "示例用户"},
    )
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 9, 8, 0, 0).astimezone(),
    )
    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **kwargs: queries.append(kwargs) or {},
    )
    monkeypatch.setattr(
        auto,
        "attempt_real_booking",
        lambda **kwargs: attempts.append(kwargs) or True,
    )
    monkeypatch.setattr(auto, "log", lambda message: None)
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)

    auto.main(["--config", str(config_path)])
    output = capsys.readouterr().out

    assert queries == []
    assert attempts == []
    assert "已到当天停止时间" in output
    assert "全天捡漏：已关闭" in output
