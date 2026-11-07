"""Health endpoints with PostgreSQL and Redis unreachable (nothing listens on the test ports)."""

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import UNIT_PG_PASSWORD, UNIT_REDIS_PASSWORD, make_client, make_settings


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with make_client(create_app(settings)) as test_client:
        yield test_client


def _assert_no_infrastructure_details(text: str, settings: Settings) -> None:
    for detail in (
        UNIT_PG_PASSWORD,
        UNIT_REDIS_PASSWORD,
        settings.postgres_user,
        settings.postgres_host,
        str(settings.postgres_port),
        str(settings.redis_port),
    ):
        assert detail not in text


def test_liveness_does_not_depend_on_infrastructure(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_unavailable(client: TestClient, settings: Settings) -> None:
    started = time.monotonic()
    response = client.get("/api/v1/health/db")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert elapsed < settings.health_check_timeout_seconds + 2
    _assert_no_infrastructure_details(response.text, settings)


def test_redis_unavailable(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/v1/health/redis")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    _assert_no_infrastructure_details(response.text, settings)


def test_readiness_reports_each_unavailable_dependency(
    client: TestClient, settings: Settings
) -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable", "redis": "unavailable"},
    }
    _assert_no_infrastructure_details(response.text, settings)


def test_app_keeps_serving_after_dependency_failures(client: TestClient) -> None:
    assert client.get("/api/v1/health/db").status_code == 503
    assert client.get("/api/v1/health/live").status_code == 200


def test_failed_check_is_logged_without_secrets(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get("/api/v1/health/ready")

    output = capsys.readouterr().out
    assert "database health check failed" in output
    assert "redis health check failed" in output
    assert UNIT_PG_PASSWORD not in output
    assert UNIT_REDIS_PASSWORD not in output


def test_api_docs_disabled_in_production() -> None:
    production = make_settings(
        app_env="production", cors_allowed_origins=["https://app.example.com"]
    )
    with make_client(create_app(production)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_api_docs_enabled_in_development() -> None:
    with make_client(create_app(make_settings(app_env="development"))) as client:
        assert client.get("/openapi.json").status_code == 200
