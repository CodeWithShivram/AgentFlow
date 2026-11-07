import uuid

import pytest

from app.core.request_id import REQUEST_ID_HEADER, resolve_request_id
from app.main import create_app
from tests.conftest import json_log_lines, make_client, make_settings


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("value", ["abc", "req-123_x.y", "A" * 64, str(uuid.uuid4())])
def test_safe_client_ids_are_kept(value: str) -> None:
    assert resolve_request_id(value) == value


@pytest.mark.parametrize(
    "value",
    [None, "", "A" * 65, "-leading-dash", "has space", "abc\r\nX-Injected: 1", "../etc", "<s>"],
)
def test_missing_or_unsafe_client_ids_are_replaced(value: str | None) -> None:
    resolved = resolve_request_id(value)

    assert resolved != value
    assert _is_uuid(resolved)


def test_generates_request_id_when_absent() -> None:
    with make_client(create_app(make_settings())) as client:
        response = client.get("/api/v1/health/live")

    assert _is_uuid(response.headers[REQUEST_ID_HEADER])


def test_propagates_valid_client_request_id() -> None:
    with make_client(create_app(make_settings())) as client:
        response = client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "client-req-1"})

    assert response.headers[REQUEST_ID_HEADER] == "client-req-1"


def test_replaces_malformed_client_request_id() -> None:
    with make_client(create_app(make_settings())) as client:
        response = client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "bad id!"})

    assert response.headers[REQUEST_ID_HEADER] != "bad id!"
    assert _is_uuid(response.headers[REQUEST_ID_HEADER])


def test_request_id_appears_in_request_log(capsys: pytest.CaptureFixture[str]) -> None:
    with make_client(create_app(make_settings())) as client:
        client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "trace-me-1"})

    logs = json_log_lines(capsys.readouterr().out)
    completed = [line for line in logs if line["message"] == "request completed"]
    assert len(completed) == 1
    assert completed[0]["request_id"] == "trace-me-1"
    assert completed[0]["path"] == "/api/v1/health/live"
    assert completed[0]["status_code"] == 200
