import requests
import pytest

from jlu_booking.api import ServerResponseError
from jlu_booking import token_validation


def _validate(monkeypatch, outcome):
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(token_validation, "query_courts", query)
    result = token_validation.validate_token_online(
        "private-token",
        query_date="2026-09-18",
        venue_name="前卫体育馆",
        sport_name="羽毛球",
        session=object(),
    )
    return result, calls


def test_read_only_token_validation_accepts_success(monkeypatch):
    result, calls = _validate(monkeypatch, {"placeArray": []})

    assert result.status == "valid"
    assert result.reason == "accepted"
    assert calls[0]["token"] == "private-token"
    assert calls[0]["query_date"] == "2026-09-18"


def test_token_validation_runs_request_guard_before_query(monkeypatch):
    class StopNow(RuntimeError):
        pass

    calls = []
    monkeypatch.setattr(
        token_validation,
        "query_courts",
        lambda **kwargs: calls.append(kwargs),
    )

    with pytest.raises(StopNow):
        token_validation.validate_token_online(
            "private-token",
            request_guard=lambda: (_ for _ in ()).throw(StopNow()),
        )

    assert calls == []


def test_token_validation_reports_query_request_and_error(monkeypatch):
    requests_started = []
    errors = []
    monkeypatch.setattr(
        token_validation,
        "query_courts",
        lambda **_kwargs: (_ for _ in ()).throw(requests.Timeout("timed out")),
    )

    result = token_validation.validate_token_online(
        "private-token",
        request_hook=requests_started.append,
        error_hook=errors.append,
    )

    assert result.status == "unavailable"
    assert requests_started == ["query"]
    assert len(errors) == 1
    assert isinstance(errors[0], requests.Timeout)


def test_read_only_token_validation_recognizes_explicit_auth_failure(monkeypatch):
    result, _ = _validate(
        monkeypatch,
        ServerResponseError({"msg": "fail", "data": "Token已失效，请重新登录"}),
    )

    assert result.status == "invalid"
    assert result.reason == "auth_rejected"


def test_read_only_token_validation_recognizes_account_block(monkeypatch):
    result, _ = _validate(
        monkeypatch,
        ServerResponseError(
            {
                "msg": "fail",
                "data": "你已被永久拉入黑名单，请勿使用脚本预定",
            }
        ),
    )

    assert result.status == "account_blocked"
    assert result.reason == "account_blocked"


def test_candidate_level_prohibition_is_not_an_account_block(monkeypatch):
    result, _ = _validate(
        monkeypatch,
        ServerResponseError(
            {"msg": "fail", "data": "该场地当前时段禁止预约"}
        ),
    )

    assert result.status == "unavailable"
    assert result.reason == "server_rejected"


def test_read_only_token_validation_treats_network_failure_as_unavailable(
    monkeypatch,
):
    result, _ = _validate(monkeypatch, requests.ConnectionError("offline"))

    assert result.status == "unavailable"
    assert result.reason == "transport"


def test_read_only_token_validation_treats_http_failure_as_unavailable(
    monkeypatch,
):
    result, _ = _validate(monkeypatch, requests.HTTPError("503 unavailable"))

    assert result.status == "unavailable"
    assert result.reason == "transport"


def test_read_only_token_validation_does_not_call_generic_rejection_invalid(
    monkeypatch,
):
    result, _ = _validate(
        monkeypatch,
        ServerResponseError({"msg": "fail", "data": "系统繁忙，请稍后再试"}),
    )

    assert result.status == "unavailable"
    assert result.reason == "server_rejected"
