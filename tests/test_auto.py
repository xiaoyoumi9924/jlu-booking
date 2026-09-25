import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from jlu_booking import auto
from jlu_booking.api import ServerResponseError
from jlu_booking.config import DEFAULT_AUTO_CONFIG, save_auto_config
from jlu_booking.token_validation import TokenValidationResult


BEIJING = ZoneInfo("Asia/Shanghai")


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
        ((7, 26, 59, 999000), ("waiting", None)),
        ((7, 27, 0, 0), ("warmup", 1.0)),
        ((7, 29, 56, 999000), ("warmup", 1.0)),
        ((7, 29, 57, 0), ("core", 0.1)),
        ((7, 32, 59, 999000), ("core", 0.1)),
        ((7, 33, 0, 0), ("finished", None)),
        ((7, 36, 0, 0), ("finished", None)),
        ((22, 30, 0, 0), ("finished", None)),
    ]

    for (hour, minute, second, microsecond), expected in cases:
        phase, interval, _ = auto.get_phase(
            datetime(2026, 1, 1, hour, minute, second, microsecond)
        )
        assert (phase, interval) == expected


def test_warmup_wait_is_truncated_at_core_start():
    now = datetime(2026, 1, 1, 7, 29, 56, 800000)

    assert auto.phase_wait_seconds(now, "warmup", 1.0) == pytest.approx(0.2)


def test_now_local_requests_asia_shanghai_from_datetime(monkeypatch):
    requested_timezones = []

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            requested_timezones.append(tz)
            return datetime(2026, 1, 1, 7, 29, 57, tzinfo=tz)

    monkeypatch.setattr(auto, "datetime", FakeDateTime)

    result = auto.now_local()

    assert requested_timezones == [ZoneInfo("Asia/Shanghai")]
    assert result.utcoffset().total_seconds() == 8 * 60 * 60


def test_phase_boundaries_convert_aware_time_to_asia_shanghai():
    utc_core_start = datetime(2026, 1, 1, 23, 29, 57, tzinfo=timezone.utc)

    phase, interval, allow_booking = auto.get_phase(utc_core_start)

    assert (phase, interval, allow_booking) == ("core", 0.1, True)


def test_target_date_uses_asia_shanghai_calendar_date(monkeypatch):
    auto.apply_runtime_settings({**DEFAULT_AUTO_CONFIG, "target_day": "今天"})
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(
            2026,
            1,
            2,
            0,
            30,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )

    assert auto.resolve_target_date() == ("2026-01-02", "今天")


def test_core_wait_keeps_existing_interval():
    now = datetime(2026, 1, 1, 7, 30)

    assert auto.phase_wait_seconds(now, "core", 0.1) == 0.1


def test_wait_until_start_does_not_oversleep_0727(monkeypatch):
    moments = iter(
        [
            datetime(2026, 1, 1, 7, 26, 59, 800000),
            datetime(2026, 1, 1, 7, 27),
        ]
    )
    sleeps = []
    monkeypatch.setattr(auto, "now_local", lambda: next(moments))
    monkeypatch.setattr(auto, "now_text", lambda: "07:26:59.800")
    monkeypatch.setattr(auto.time, "sleep", sleeps.append)

    auto.wait_until_start()

    assert sleeps == [pytest.approx(0.2)]


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
        lambda: datetime(2026, 9, 10, 7, 29, tzinfo=BEIJING),
    )
    monkeypatch.setattr(auto.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "log", event_logs.append)
    monkeypatch.setattr(auto, "update_run_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "extract_available_slots", lambda data: data)
    return sleeps, event_logs


def test_finished_loop_records_no_result_status(monkeypatch):
    statuses = []
    monkeypatch.setattr(
        auto,
        "get_phase",
        lambda _now: ("finished", None, False),
    )
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 10, 22, 30, tzinfo=BEIJING),
    )
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert statuses == [
        ("no_result", {"target_date": "2026-09-11", "phase": "finished"})
    ]


def test_successful_loop_records_success_status(monkeypatch):
    target = _target()
    _prepare_loop_test(monkeypatch, [("core", 0.1, True)])
    statuses = []
    queries = []
    _install_query_outcomes(monkeypatch, [[target]], queries)
    monkeypatch.setattr(auto, "attempt_real_booking", lambda **_kwargs: True)
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert statuses == [
        ("running", {"target_date": "2026-09-11", "phase": "core"}),
        ("success", {"target_date": "2026-09-11", "phase": "core"}),
    ]


def test_immediate_loop_queries_after_deadline_until_success(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(monkeypatch, [])
    monkeypatch.setattr(auto, "now_local", lambda: datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING))
    queries = []
    _install_query_outcomes(monkeypatch, [[], [target]], queries)
    guards = []
    monkeypatch.setattr(auto, "attempt_real_booking", lambda **kw: guards.append(kw["request_guard"]) or True)

    auto.run_booking_loop(
        query_date="2026-09-11", companion_id=123, companion_name="示例用户",
        token="example-token", session=object(), immediate=True,
    )

    assert len(queries) == 2
    assert sleeps == [auto.WARMUP_INTERVAL]
    assert guards == [None]


@pytest.mark.parametrize(
    ("server_message", "expected_status"),
    [
        ("Token已失效，请重新登录", "token_invalid"),
        ("当天最大预约次数：1次，剩余预约次数：0次", "daily_limit"),
        (
            "你已被永久拉入黑名单，请勿使用脚本预定，如需解封，请联系管理员",
            "account_blocked",
        ),
    ],
)
def test_terminal_query_failure_records_run_status(
    monkeypatch,
    server_message,
    expected_status,
):
    _prepare_loop_test(monkeypatch, [("core", 0.1, True)])
    statuses = []
    monkeypatch.setattr(
        auto,
        "query_courts",
        lambda **_kwargs: (_ for _ in ()).throw(
            ServerResponseError({"msg": "fail", "data": server_message})
        ),
    )
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert statuses == [
        ("running", {"target_date": "2026-09-11", "phase": "core"}),
        (expected_status, {"target_date": "2026-09-11", "phase": "core"}),
    ]


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
            ("warmup", 1.0, False),
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
    assert sleeps == [1.0]


def test_warmup_response_after_core_start_does_not_add_stale_wait(monkeypatch):
    settings = {
        **DEFAULT_AUTO_CONFIG,
        "companion_student_number": "example-1234",
        "real_booking_enabled": True,
    }
    auto.apply_runtime_settings(settings)
    clock = [datetime(2026, 9, 20, 7, 29, 56, 800000, tzinfo=BEIJING)]
    target = _target()
    sleeps = []
    queries = []

    monkeypatch.setattr(auto, "now_local", lambda: clock[0])
    monkeypatch.setattr(auto, "now_text", lambda: "07:29:57.100")
    monkeypatch.setattr(auto.time, "sleep", sleeps.append)
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "log", lambda _message: None)
    monkeypatch.setattr(auto, "update_run_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "extract_available_slots", lambda data: data)

    def query(**kwargs):
        queries.append(kwargs)
        clock[0] = datetime(2026, 9, 20, 7, 29, 57, 100000, tzinfo=BEIJING)
        return [target]

    monkeypatch.setattr(auto, "query_courts", query)
    monkeypatch.setattr(auto, "attempt_real_booking", lambda **_kwargs: True)

    auto.run_booking_loop(
        query_date="2026-09-21",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 2
    assert sleeps == []


def test_warmup_query_rejection_keeps_one_second_interval(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("warmup", 1.0, False), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [
            ServerResponseError({"msg": "fail", "data": "未知服务器错误"}),
            [target],
        ],
        queries,
    )
    monkeypatch.setattr(
        auto,
        "attempt_real_booking",
        lambda **kwargs: attempts.append(kwargs) or True,
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 2
    assert [item["slot"] for item in attempts] == [target]
    assert sleeps == [1.0]


def test_not_open_requeries_immediately_without_sleep(monkeypatch):
    target = _target()
    lower_priority = _slot("羽毛球1", "ymq1", "19:30", "21:30")
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
        [[lower_priority, target], [lower_priority, target]],
        queries,
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

    assert len(queries) == 2
    assert len(attempts) == 2
    assert attempts[0]["slot"] == attempts[1]["slot"] == target
    assert sleeps == []
    assert any("CORE_RETRY | category=not_open" in line for line in event_logs)
    assert not any("TARGET_FAILED" in line for line in event_logs)


def test_generic_server_rejected_retries_latest_top_priority_without_history(
    monkeypatch,
):
    high_priority = _slot("羽毛球3", "ymq3", "17:30", "19:30")
    lower_priority = _slot("羽毛球1", "ymq1", "19:30", "21:30")
    sleeps, event_logs = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [
            [lower_priority, high_priority],
            [lower_priority, high_priority],
        ],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "未知服务器错误"})
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
    assert [item["slot"] for item in attempts] == [
        high_priority,
        high_priority,
    ]
    assert sleeps == []
    assert not any(
        "OPEN_DETECTED | result=server_rejected" in line
        for line in event_logs
    )
    assert not any("INVALIDATED" in line for line in event_logs)
    assert not any("TARGET_FAILED" in line for line in event_logs)
    assert any(
        "CORE_RETRY | category=server_rejected | priority=unchanged"
        in line
        for line in event_logs
    )


def test_core_empty_query_retries_immediately_without_sleep(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(monkeypatch, [[], [target]], queries)
    monkeypatch.setattr(
        auto,
        "attempt_real_booking",
        lambda **kwargs: attempts.append(kwargs) or True,
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 2
    assert [item["slot"] for item in attempts] == [target]
    assert sleeps == []


def test_core_dry_run_retries_without_sleep(monkeypatch):
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("finished", None, False)],
    )
    monkeypatch.setattr(auto, "REAL_BOOKING_ENABLED", False)
    queries = []
    _install_query_outcomes(monkeypatch, [[]], queries)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=0,
        companion_name="",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 1
    assert sleeps == []


def test_generic_failure_does_not_retry_a_target_missing_from_fresh_query(
    monkeypatch,
):
    stale_target = _target("羽毛球3")
    current_target = _target("羽毛球5")
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [[stale_target], [current_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "未知服务器错误"})
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
    assert [item["slot"] for item in attempts] == [
        stale_target,
        current_target,
    ]
    assert sleeps == []


def test_deadline_after_canbook_prevents_freebuy(monkeypatch):
    target = _target()
    calls = []
    guards = iter([None, auto.CoreWindowEnded()])

    monkeypatch.setattr(
        auto,
        "can_book",
        lambda **_kwargs: calls.append("canBook") or {"msg": "success"},
    )
    monkeypatch.setattr(
        auto,
        "book_place",
        lambda **_kwargs: calls.append("freeBuyPlace") or {"msg": "success"},
    )
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)

    def request_guard():
        outcome = next(guards)
        if outcome is not None:
            raise outcome

    with pytest.raises(auto.CoreWindowEnded):
        auto.attempt_real_booking(
            query_date="2026-09-11",
            slot=target,
            companion_id=123,
            companion_name="示例用户",
            token="example-token",
            session=object(),
            request_id=1,
            request_guard=request_guard,
        )

    assert calls == ["canBook"]


def test_query_returning_at_deadline_does_not_start_canbook(monkeypatch):
    settings = {
        **DEFAULT_AUTO_CONFIG,
        "companion_student_number": "example-1234",
        "real_booking_enabled": True,
    }
    auto.apply_runtime_settings(settings)
    target = _target()
    moments = iter(
        [
            datetime(2026, 9, 20, 7, 32, 59, 900000, tzinfo=BEIJING),
            datetime(2026, 9, 20, 7, 32, 59, 900000, tzinfo=BEIJING),
            datetime(2026, 9, 20, 7, 33, tzinfo=BEIJING),
        ]
    )
    canbook_calls = []
    monkeypatch.setattr(auto, "now_local", lambda: next(moments))
    monkeypatch.setattr(auto, "now_text", lambda: "07:33:00.000")
    monkeypatch.setattr(auto, "query_courts", lambda **_kwargs: [target])
    monkeypatch.setattr(auto, "extract_available_slots", lambda data: data)
    monkeypatch.setattr(
        auto,
        "can_book",
        lambda **kwargs: canbook_calls.append(kwargs),
    )
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "log", lambda _message: None)
    monkeypatch.setattr(auto, "update_run_status", lambda *_args, **_kwargs: None)

    auto.run_booking_loop(
        query_date="2026-09-21",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert canbook_calls == []


def test_explicit_target_unavailable_invalidates_only_that_candidate(
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
    assert not any(
        "OPEN_DETECTED | result=target_unavailable" in line
        for line in event_logs
    )
    assert any("INVALIDATED | target=" in line for line in event_logs)
    assert any(
        "TARGET_FAILED | category=target_unavailable" in line
        for line in event_logs
    )


def test_new_candidate_can_join_after_a_fresh_query(monkeypatch):
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
    assert any("INVALIDATED | target=" in line for line in event_logs)
    assert not any("ROUND_START" in line for line in event_logs)


def test_invalidated_candidate_is_not_retried_while_query_still_lists_it(
    monkeypatch,
):
    stale_target = _target()
    fresh_target = _target("羽毛球5")
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
        [[stale_target], [stale_target], [stale_target, fresh_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError(
                {"msg": "fail", "data": "该场地已被预约"}
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

    assert [item["slot"] for item in attempts] == [stale_target, fresh_target]
    assert len(queries) == 3
    assert sleeps == []
    assert any("INVALIDATED" in line and "ymq3" in line for line in event_logs)


def test_invalidated_candidate_is_reenabled_only_after_disappear_and_reappear(
    monkeypatch,
):
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
    _install_query_outcomes(monkeypatch, [[target], [], [target]], queries)

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

    assert [item["slot"] for item in attempts] == [target, target]
    assert len(queries) == 3
    assert sleeps == []
    assert any("INVALIDATED_ABSENT" in line for line in event_logs)
    assert any("REAPPEARED" in line for line in event_logs)


def test_console_try_target_matches_the_slot_actually_submitted(
    monkeypatch,
    capsys,
):
    stale_target = _target()
    fresh_target = _target("羽毛球5")
    _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True), ("core", 0.1, True)],
    )
    queries = []
    attempts = []
    _install_query_outcomes(
        monkeypatch,
        [[stale_target, fresh_target], [stale_target, fresh_target]],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError(
                {"msg": "fail", "data": "该场地已被预约"}
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

    output = capsys.readouterr().out
    assert [item["slot"] for item in attempts] == [stale_target, fresh_target]
    assert "目标：羽毛球3 17:30-19:30 [ymq3]" in output
    assert "TRY | 羽毛球5 17:30-19:30 [ymq5]" in output


def test_not_open_after_target_unavailable_does_not_change_candidate_history(
    monkeypatch,
):
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
        [
            [first_target],
            [first_target, next_target],
            [first_target, next_target],
        ],
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
    assert len(queries) == 3
    assert sleeps == []
    assert sum("OPEN_DETECTED | result=success" in line for line in event_logs) == 1
    assert not any(
        "OPEN_DETECTED | result=not_open" in line for line in event_logs
    )


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


def test_rate_limit_and_precheck_transport_wait_then_query_fresh(monkeypatch):
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
    _install_query_outcomes(monkeypatch, [[target], [target], [target]], queries)

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

    assert len(queries) == 3
    assert [item["slot"] for item in attempts] == [target, target, target]
    assert sleeps == [5.0, 0.1]


@pytest.mark.parametrize(
    ("retryable_error", "expected_delay"),
    [
        (
            ServerResponseError({"msg": "fail", "data": "请求过于频繁"}),
            5.0,
        ),
        (requests.ConnectionError("canBook disconnected"), 0.1),
    ],
)
def test_post_open_retryable_error_waits_then_requeries_fresh_state(
    monkeypatch,
    retryable_error,
    expected_delay,
):
    first_target = _target("羽毛球3")
    retry_target = _target("羽毛球4")
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
        [
            [first_target, retry_target],
            [first_target, retry_target],
            [first_target, retry_target],
        ],
        queries,
    )

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "该场地已被预约"})
        if len(attempts) == 2:
            raise retryable_error
        return True

    monkeypatch.setattr(auto, "attempt_real_booking", attempt)

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 3
    assert [item["slot"] for item in attempts] == [
        first_target,
        retry_target,
        retry_target,
    ]
    assert sleeps == [expected_delay]
    assert any("RETRY_REFRESH" in line for line in event_logs)


def test_dynamic_logs_include_counts_without_sensitive_values(monkeypatch):
    first_target = _target("羽毛球3")
    next_target = _target("羽毛球5")
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
    visible = [first_target, next_target, invalid_target]
    _install_query_outcomes(monkeypatch, [visible, visible], queries)
    attempts = []

    def attempt(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ServerResponseError({"msg": "fail", "data": "该场地已被预约"})
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
    assert (
        "QUERY | visible=3 | invalidated=1 | eligible=1"
    ) in combined
    assert "TRY | target=羽毛球5 17:30-19:30 [ymq5]" in combined
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


def test_auto_prompt_defers_saving_until_online_validation(monkeypatch):
    monkeypatch.setattr(auto, "resolve_token", lambda: ("", "none"))
    monkeypatch.setattr(auto.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(auto.getpass, "getpass", lambda _prompt: "new-token")
    saved = []
    monkeypatch.setattr(auto, "save_token", lambda token: saved.append(token))

    assert auto.get_runtime_token() == ("new-token", "prompt")
    assert saved == []


def test_runtime_token_is_saved_only_after_successful_online_validation(
    monkeypatch,
):
    saved = []
    event_logs = []
    monkeypatch.setattr(
        auto,
        "validate_token_online",
        lambda *_args, **_kwargs: TokenValidationResult("valid", "accepted"),
    )
    monkeypatch.setattr(auto, "save_token", lambda token: saved.append(token))
    monkeypatch.setattr(auto, "log", event_logs.append)

    source = auto.validate_runtime_token(
        "new-token",
        "prompt",
        query_date="2026-09-18",
        session=object(),
    )

    assert source == "prompt_saved"
    assert saved == ["new-token"]
    assert event_logs == ["TOKEN_CHECK | valid"]


@pytest.mark.parametrize("status", ["invalid", "unavailable"])
def test_runtime_token_failure_is_classified_without_saving(monkeypatch, status):
    saved = []
    monkeypatch.setattr(
        auto,
        "validate_token_online",
        lambda *_args, **_kwargs: TokenValidationResult(status, "test"),
    )
    monkeypatch.setattr(auto, "save_token", lambda token: saved.append(token))
    monkeypatch.setattr(auto, "log", lambda _message: None)

    with pytest.raises(auto.RuntimeTokenValidationError) as exc_info:
        auto.validate_runtime_token(
            "new-token",
            "prompt",
            query_date="2026-09-18",
            session=object(),
        )

    assert exc_info.value.status == status
    assert saved == []


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
    statuses = []
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(DEFAULT_AUTO_CONFIG, config_path)
    monkeypatch.setattr(auto, "STATE_DIR", tmp_path / "state")

    def fail_token_read():
        raise AssertionError("incomplete settings must stop before reading Token")

    monkeypatch.setattr(auto, "get_runtime_token", fail_token_read)
    monkeypatch.setattr(
        auto,
        "resolve_target_date",
        lambda: ("2026-09-11", "明天"),
    )
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    with pytest.raises(SystemExit) as exc_info:
        auto.main(["--config", str(config_path)])

    message = str(exc_info.value)
    assert "自动预约配置尚未完成" in message
    assert "同行人学工号为空" in message
    assert "jlu-booking" in message
    assert "--dry-run" in message
    assert statuses == [
        ("starting", {"target_date": "2026-09-11", "phase": "startup"}),
        ("error", {"target_date": "2026-09-11", "phase": "configuration"}),
    ]


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
        ("ACCOUNT_BLOCKED", "account_blocked"),
        ("你已被永久拉入黑名单，请勿使用脚本预定", "account_blocked"),
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
        "服务器繁忙",
        "系统繁忙",
        "请稍后再试",
        "系统繁忙，请稍后再试",
        "系统繁忙，请重新选择场地",
    ],
)
def test_ambiguous_busy_messages_are_generic_server_rejections(message):
    error = ServerResponseError({"msg": "fail", "data": message})

    assert auto.classify_booking_error(error) == "server_rejected"


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 429 Too Many Requests",
        "请求过于频繁",
        "操作过于频繁",
        "访问过于频繁",
        "已触发接口频率限制",
    ],
)
def test_explicit_rate_limit_messages_keep_rate_limit_backoff(message):
    error = ServerResponseError({"msg": "fail", "data": message})

    assert auto.classify_booking_error(error) == "rate_limited"


def test_candidate_level_prohibition_is_not_account_blocked():
    error = ServerResponseError(
        {"msg": "fail", "data": "该场地当前时段禁止预约"}
    )

    assert auto.is_account_blocked_error(error) is False
    assert auto.classify_booking_error(error) == "server_rejected"


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


def test_ambiguous_busy_reselect_is_a_generic_server_rejection():
    error = ServerResponseError(
        {"msg": "fail", "data": "系统繁忙，请重新选择"}
    )

    assert auto.classify_booking_error(error) == "server_rejected"


def test_request_timing_uses_a_separate_log(tmp_path, monkeypatch):
    timing_path = tmp_path / "request_timing.log"
    monkeypatch.setattr(auto, "TIMING_LOG_FILE", timing_path)
    monkeypatch.setattr(
        auto,
        "now_local",
        lambda: datetime(2026, 9, 10, 7, 30, tzinfo=BEIJING),
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


def test_run_statistics_count_requests_and_failures(monkeypatch):
    stats = auto.RunStatistics(
        started_at=datetime(2026, 9, 20, 7, 27, tzinfo=BEIJING)
    )
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)

    auto.timed_api_call(1, "query", lambda: {}, stats=stats)

    failures = [
        (
            "freeBuyPlace",
            ServerResponseError({"msg": "fail", "data": "预约尚未开放"}),
        ),
        (
            "freeBuyPlace",
            ServerResponseError({"msg": "fail", "data": "该场地已被预约"}),
        ),
        (
            "canBook",
            ServerResponseError({"msg": "fail", "data": "未知服务器错误"}),
        ),
    ]
    for request_id, (endpoint, error) in enumerate(failures, start=2):
        with pytest.raises(ServerResponseError):
            auto.timed_api_call(
                request_id,
                endpoint,
                lambda error=error: (_ for _ in ()).throw(error),
                stats=stats,
            )

    assert stats.query_count == 1
    assert stats.canbook_count == 1
    assert stats.freebuy_count == 2
    assert stats.not_open_count == 1
    assert stats.target_unavailable_count == 1
    assert stats.server_rejected_count == 1


def test_run_statistics_distinguish_http_and_transport_errors(monkeypatch):
    stats = auto.RunStatistics(
        started_at=datetime(2026, 9, 20, 7, 27, tzinfo=BEIJING)
    )
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    http_error = RuntimeError("请求学校服务器失败")
    http_error.__cause__ = requests.HTTPError("503 Server Error")
    transport_error = RuntimeError("请求学校服务器失败")
    transport_error.__cause__ = requests.Timeout("timed out")

    for request_id, error in enumerate((http_error, transport_error), start=1):
        with pytest.raises(RuntimeError):
            auto.timed_api_call(
                request_id,
                "query",
                lambda error=error: (_ for _ in ()).throw(error),
                stats=stats,
            )

    assert stats.http_error_count == 1
    assert stats.transport_error_count == 1


def test_http_429_is_counted_as_http_error(monkeypatch):
    stats = auto.RunStatistics(
        started_at=datetime(2026, 9, 20, 7, 27, tzinfo=BEIJING)
    )
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    error = RuntimeError("请求学校服务器失败：429 Too Many Requests")
    error.__cause__ = requests.HTTPError("429 Too Many Requests")

    with pytest.raises(RuntimeError):
        auto.timed_api_call(
            1,
            "query",
            lambda: (_ for _ in ()).throw(error),
            stats=stats,
        )

    assert stats.http_error_count == 1


def test_statistics_report_prints_stop_reason_and_success_target(capsys):
    stats = auto.RunStatistics(
        started_at=datetime(2026, 9, 20, 7, 27, tzinfo=BEIJING),
        stop_reason="BOOKING_SUCCESS",
        booked_slot=_target(),
    )

    auto.report_run_statistics(
        stats,
        ended_at=datetime(2026, 9, 20, 7, 30, 1, tzinfo=BEIJING),
    )

    output = capsys.readouterr().out
    assert "本次自动预约统计" in output
    assert "高速开始：07:29:57" in output
    assert "STOP_REASON: BOOKING_SUCCESS" in output
    assert "场地：羽毛球3" in output
    assert "时间：17:30-19:30" in output


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


def test_booking_outcome_unknown_stops_loop_without_requery(monkeypatch):
    target = _target()
    sleeps, _ = _prepare_loop_test(
        monkeypatch,
        [("core", 0.1, True)],
    )
    queries = []
    statuses = []
    _install_query_outcomes(monkeypatch, [[target]], queries)
    monkeypatch.setattr(
        auto,
        "attempt_real_booking",
        lambda **_kwargs: (_ for _ in ()).throw(
            auto.BookingOutcomeUnknown("result unknown")
        ),
    )
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.run_booking_loop(
        query_date="2026-09-11",
        companion_id=123,
        companion_name="示例用户",
        token="example-token",
        session=object(),
    )

    assert len(queries) == 1
    assert statuses == [
        ("running", {"target_date": "2026-09-11", "phase": "core"}),
        (
            "submission_unknown",
            {"target_date": "2026-09-11", "phase": "core"},
        ),
    ]
    assert sleeps == []


def test_existing_success_state_stops_before_token_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    statuses = []
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(DEFAULT_AUTO_CONFIG, config_path)
    success_path = tmp_path / "success.json"
    monkeypatch.setattr(
        auto,
        "resolve_target_date",
        lambda: ("2026-09-11", "明天"),
    )
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
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.main(["--config", str(config_path)])
    output = capsys.readouterr().out

    assert "本次直接退出" in output
    assert str(success_path) in output
    assert "本次自动预约统计" in output
    assert "STOP_REASON: EXISTING_SUCCESS_STATE" in output
    assert statuses == [
        ("starting", {"target_date": "2026-09-11", "phase": "startup"}),
        ("success", {"target_date": "2026-09-11", "phase": "existing_state"}),
    ]


@pytest.mark.parametrize(
    ("validation_status", "expected_status"),
    [
        ("invalid", "token_invalid"),
        ("unavailable", "network_unavailable"),
    ],
)
def test_main_records_token_validation_failure_status(
    tmp_path,
    monkeypatch,
    validation_status,
    expected_status,
):
    config_path = tmp_path / "auto_booking.json"
    save_auto_config(
        {
            **DEFAULT_AUTO_CONFIG,
            "companion_student_number": "example-1234",
        },
        config_path,
    )
    statuses = []

    monkeypatch.setenv("JLU_BOOKING_TOKEN", "example-token")
    monkeypatch.setattr(
        auto,
        "resolve_target_date",
        lambda: ("2026-09-11", "明天"),
    )
    monkeypatch.setattr(auto, "existing_success_state_path", lambda _date: None)
    monkeypatch.setattr(
        auto,
        "validate_runtime_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            auto.RuntimeTokenValidationError(validation_status)
        ),
    )
    monkeypatch.setattr(
        auto,
        "update_run_status",
        lambda status, **fields: statuses.append((status, fields)),
    )

    auto.main(["--config", str(config_path)])

    assert statuses == [
        ("starting", {"target_date": "2026-09-11", "phase": "startup"}),
        (
            expected_status,
            {"target_date": "2026-09-11", "phase": "token_check"},
        ),
    ]


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
    monkeypatch.setattr(
        auto,
        "validate_token_online",
        lambda *_args, **_kwargs: TokenValidationResult("valid", "accepted"),
    )
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
        lambda: datetime(2026, 9, 9, 7, 30, tzinfo=BEIJING),
    )
    monkeypatch.setattr(auto, "query_courts", lambda **_kwargs: {})
    monkeypatch.setattr(auto, "extract_available_slots", lambda _data: [target])
    monkeypatch.setattr(auto, "log", lambda message: None)
    monkeypatch.setattr(auto, "timing_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auto, "update_run_status", lambda *_args, **_kwargs: None)

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
