import pytest

from jlu_booking.api import (
    ServerResponseError,
    _check_success,
    _request_json,
    _redact_sensitive_text,
    extract_available_slots,
)


def test_token_is_redacted_from_request_url():
    message = "request failed: https://example.test/path?token=very-secret&shopNum=0002"

    redacted = _redact_sensitive_text(message)

    assert "very-secret" not in redacted
    assert "token=<REDACTED>" in redacted
    assert "shopNum=0002" in redacted


def test_extract_available_slots_only_returns_open_entries():
    data = {
        "placeArray": [
            {
                "projectName": {"name": "乒乓球3", "id": 3, "shortname": "ppq3"},
                "projectInfo": [
                    {"state": 1, "starttime": "17:30", "endtime": "19:30"},
                    {"state": 0, "starttime": "19:30", "endtime": "21:30"},
                ],
            }
        ]
    }

    assert extract_available_slots(data) == [
        {
            "court_name": "乒乓球3",
            "court_id": 3,
            "place_short_name": "ppq3",
            "start": "17:30",
            "end": "19:30",
        }
    ]


def test_server_response_error_keeps_structured_result():
    result = {"msg": "fail", "data": "example failure"}

    with pytest.raises(ServerResponseError) as error:
        _check_success(result)

    assert error.value.result == result


def test_request_json_uses_supplied_session():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"msg": "success", "data": {}}

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response()

    session = Session()
    result = _request_json(
        "GET",
        "https://example.test/api",
        session=session,
        params={"example": "value"},
    )

    assert result == {"msg": "success", "data": {}}
    assert calls == [
        (
            "GET",
            "https://example.test/api",
            {"timeout": 10, "params": {"example": "value"}},
        )
    ]
