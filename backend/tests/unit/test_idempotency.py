from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.request_id import REQUEST_ID_HEADER
from app.core.sensitive_data import find_sensitive_keys, is_sensitive_key
from app.main import create_app
from app.services.idempotency import IdempotencyKeyReusedError, request_fingerprint
from tests.conftest import make_client, make_settings


def test_fingerprint_ignores_key_order_and_whitespace() -> None:
    assert request_fingerprint({"a": 1, "b": [1, 2]}) == request_fingerprint({"b": [1, 2], "a": 1})
    assert len(request_fingerprint({"a": 1})) == 64


def test_fingerprint_distinguishes_values_and_types() -> None:
    assert request_fingerprint({"a": 1}) != request_fingerprint({"a": 2})
    assert request_fingerprint({"a": 1}) != request_fingerprint({"a": "1"})


def test_fingerprint_rejects_non_json_values() -> None:
    with pytest.raises(TypeError):
        request_fingerprint({"a": object()})
    with pytest.raises(ValueError):
        request_fingerprint({"a": float("nan")})


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "password_hash",
        "client_secret",
        "apiKey",
        "X-API-Key",
        "Authorization",
        "set-cookie",
        "access_token",
        "refreshToken",
        "token",
        "credentials",
        "private_key",
    ],
)
def test_sensitive_keys_are_detected(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["workflow_id", "status", "input_tokens", "output_tokens", "token_usage", "prompt"]
)
def test_ordinary_keys_are_not_sensitive(key: str) -> None:
    assert not is_sensitive_key(key)


def test_find_sensitive_keys_reports_nested_paths() -> None:
    body = {"user": {"email": "a@b.c", "password": "x"}, "items": [{"ok": 1}, {"api_key": "k"}]}

    assert find_sensitive_keys(body) == ["$.user.password", "$.items[1].api_key"]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(make_settings())

    @app.post("/test/idempotent")
    async def reused() -> None:
        raise IdempotencyKeyReusedError()

    with make_client(app) as test_client:
        yield test_client


def test_key_reuse_maps_to_409_standard_error(client: TestClient) -> None:
    response = client.post("/test/idempotent", headers={REQUEST_ID_HEADER: "idem-1"})

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "This idempotency key was already used with a different request.",
            "request_id": "idem-1",
            "details": {},
        }
    }
