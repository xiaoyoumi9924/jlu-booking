import requests

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


def test_read_only_token_validation_recognizes_explicit_auth_failure(monkeypatch):
    result, _ = _validate(
        monkeypatch,
        ServerResponseError({"msg": "fail", "data": "Token已失效，请重新登录"}),
    )

    assert result.status == "invalid"
    assert result.reason == "auth_rejected"


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
