from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr

from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app
from tests.conftest import UNIT_PG_PASSWORD, json_log_lines, make_client, make_settings

SUBMITTED_SECRET = "submitted-password-value"


class Credentials(BaseModel):
    password: SecretStr
    attempts: int


def _app_with_test_routes() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/test/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.post("/test/login")
    async def login(body: Credentials) -> dict[str, int]:
        return {"attempts": body.attempts}

    @app.get("/test/conflict")
    async def conflict() -> None:
        raise HTTPException(status_code=409, detail="Workflow already started.")

    @app.get("/test/upstream")
    async def upstream() -> None:
        raise HTTPException(status_code=503, detail="internal host db-7 refused")

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError(f"boom postgresql://agentflow:pw@db/x {UNIT_PG_PASSWORD}")

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client(_app_with_test_routes(), raise_server_exceptions=False) as test_client:
        yield test_client


def _assert_error_shape(body: dict[str, Any], code: str, request_id: str) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == request_id


def test_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/missing")

    assert response.status_code == 404
    _assert_error_shape(response.json(), "NOT_FOUND", response.headers[REQUEST_ID_HEADER])


def test_method_not_allowed(client: TestClient) -> None:
    response = client.post("/api/v1/health/live")

    assert response.status_code == 405
    _assert_error_shape(response.json(), "METHOD_NOT_ALLOWED", response.headers[REQUEST_ID_HEADER])


def test_http_exception_keeps_client_safe_detail(client: TestClient) -> None:
    response = client.get("/test/conflict")

    assert response.status_code == 409
    _assert_error_shape(response.json(), "CONFLICT", response.headers[REQUEST_ID_HEADER])
    assert response.json()["error"]["message"] == "Workflow already started."


def test_server_side_http_exception_hides_detail(client: TestClient) -> None:
    response = client.get("/test/upstream")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Service Unavailable"
    assert "db-7" not in response.text


def test_validation_error_lists_fields_without_submitted_values(client: TestClient) -> None:
    response = client.post(
        "/test/login", json={"password": SUBMITTED_SECRET, "attempts": "not-a-number"}
    )

    assert response.status_code == 422
    body = response.json()
    _assert_error_shape(body, "VALIDATION_ERROR", response.headers[REQUEST_ID_HEADER])
    errors = body["error"]["details"]["errors"]
    assert [err["loc"] for err in errors] == [["body", "attempts"]]
    assert set(errors[0]) == {"loc", "message", "type"}
    assert SUBMITTED_SECRET not in response.text
    assert "not-a-number" not in response.text


def test_path_validation_error(client: TestClient) -> None:
    response = client.get("/test/items/abc")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["path", "item_id"]


def test_unexpected_exception_returns_generic_500(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    response = client.get("/test/crash", headers={REQUEST_ID_HEADER: "crash-1"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "crash-1"
    body = response.json()
    _assert_error_shape(body, "INTERNAL_ERROR", "crash-1")
    assert body["error"]["message"] == "An unexpected error occurred."
    for leaked in ("boom", "RuntimeError", "Traceback", UNIT_PG_PASSWORD, "agentflow:pw"):
        assert leaked not in response.text

    output = capsys.readouterr().out
    logged = [line for line in json_log_lines(output) if line["message"] == "unhandled exception"]
    assert len(logged) == 1
    assert logged[0]["request_id"] == "crash-1"
    assert logged[0]["exception"]["type"] == "RuntimeError"
    assert UNIT_PG_PASSWORD not in output
    assert "agentflow:pw" not in output


def test_unexpected_exception_is_not_swallowed() -> None:
    app = _app_with_test_routes()

    with make_client(app, raise_server_exceptions=True) as test_client:
        with pytest.raises(RuntimeError):
            test_client.get("/test/crash")
