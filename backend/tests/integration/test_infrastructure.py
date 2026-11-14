"""Against the real local PostgreSQL and Redis (scripts/infra.ps1 up)."""

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

import psycopg
import pytest
import redis
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory, get_db_session
from app.execution import heartbeat
from app.main import create_app
from tests.conftest import make_client
from tests.integration.conftest import INFRA_HINT

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_local_infrastructure() -> None:
    """Fail fast with an actionable message instead of opaque 503 assertions."""
    try:
        settings = Settings(app_env="test")
        _postgres_connections(settings)
        _redis_connections(settings)
    except (ValueError, psycopg.Error, redis.RedisError, OSError) as exc:
        pytest.fail(f"{INFRA_HINT} ({type(exc).__name__})", pytrace=False)


@pytest.fixture
def settings() -> Settings:
    # Credentials come from the repo-root .env; a unique name identifies this test's connections.
    # These tests assert "healthy when dependencies are up", not latency: a cold connection on a
    # saturated host exceeded the 2 s production default, so allow 10 s here. The timeout path
    # itself is covered by unit tests (1 s against closed ports).
    return Settings(
        app_env="test",
        service_name=f"agentflow-it-{uuid.uuid4().hex[:8]}",
        health_check_timeout_seconds=10.0,
    )


def _app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/test/db-session")
    async def db_session(
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, int]:
        return {"value": (await session.execute(text("SELECT 1"))).scalar_one()}

    return app


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with make_client(_app(settings)) as test_client:
        yield test_client


def _postgres_connections(settings: Settings) -> int:
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        connect_timeout=5,
    ) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
            (settings.service_name,),
        ).fetchone()
    assert row is not None
    count: int = row[0]
    return count


def _redis_connections(settings: Settings) -> int:
    admin = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        socket_timeout=5,
    )
    try:
        clients = admin.client_list()
    finally:
        admin.close()
    return sum(1 for c in clients if c.get("name") == settings.service_name)


def _eventually(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@pytest.mark.parametrize(
    "path", ["/api/v1/health/live", "/api/v1/health/db", "/api/v1/health/redis"]
)
def test_health_endpoint_ok(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.fixture
def relay_heartbeat(settings: Settings) -> Iterator[Callable[[float, int], None]]:
    """Writes the relay heartbeat (ADR-0027) as a worker would; removed afterwards."""
    client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password.get_secret_value(),
    )

    def write(seconds_ago: float, oldest_due: int) -> None:
        value = heartbeat.encode(at=time.time() - seconds_ago, oldest_due=oldest_due)
        client.set(heartbeat.KEY, value, ex=heartbeat.TTL_SECONDS)

    try:
        yield write
    finally:
        client.delete(heartbeat.KEY)
        client.close()


def test_readiness_ok(
    client: TestClient, settings: Settings, relay_heartbeat: Callable[[float, int], None]
) -> None:
    relay_heartbeat(1, 0)
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok", "workers": "ok"},
    }
    for secret in settings.secret_values():
        assert secret not in response.text


def test_db_session_dependency_executes_queries(client: TestClient) -> None:
    response = client.get("/test/db-session")

    assert response.status_code == 200
    assert response.json() == {"value": 1}


@pytest.mark.anyio
async def test_session_returns_connection_to_pool(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert engine.pool.checkedout() == 1  # type: ignore[attr-defined]
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("seconds_ago", "oldest_due", "expected"),
    [(120, 0, "unavailable"), (1, 900, "lagging")],
)
def test_readiness_reports_silent_or_lagging_workers(
    client: TestClient,
    relay_heartbeat: Callable[[float, int], None],
    seconds_ago: float,
    oldest_due: int,
    expected: str,
) -> None:
    relay_heartbeat(seconds_ago, oldest_due)
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["workers"] == expected
    assert response.json()["checks"]["database"] == "ok"


def test_shutdown_closes_database_and_redis_connections(
    settings: Settings, relay_heartbeat: Callable[[float, int], None]
) -> None:
    relay_heartbeat(1, 0)
    with make_client(_app(settings)) as client:
        assert client.get("/api/v1/health/ready").status_code == 200
        assert _postgres_connections(settings) >= 1
        assert _redis_connections(settings) >= 1

    assert _eventually(lambda: _postgres_connections(settings) == 0)
    assert _eventually(lambda: _redis_connections(settings) == 0)
