"""Session dependency behaviour, startup without infrastructure and shutdown cleanup."""

import asyncio
import sys
from types import SimpleNamespace, TracebackType
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import (
    create_engine,
    create_session_factory,
    ensure_psycopg_compatible_event_loop,
    get_db_session,
)
from app.main import create_app
from tests.conftest import make_client, make_settings


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.closed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _request_with_factory(session: FakeSession) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=lambda: session))
    )


@pytest.mark.anyio
async def test_session_dependency_closes_session_after_request() -> None:
    session = FakeSession()
    dependency = get_db_session(_request_with_factory(session))

    yielded: object = await anext(dependency)
    assert yielded is session
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert session.closed
    assert not session.rolled_back


@pytest.mark.anyio
async def test_session_dependency_rolls_back_and_reraises_on_error() -> None:
    session = FakeSession()
    dependency = get_db_session(_request_with_factory(session))
    await anext(dependency)

    with pytest.raises(RuntimeError, match="handler failed"):
        await dependency.athrow(RuntimeError("handler failed"))

    assert session.rolled_back
    assert session.closed


@pytest.mark.anyio
async def test_session_factory_creates_async_sessions_without_connecting() -> None:
    engine = create_engine(make_settings())
    try:
        async with create_session_factory(engine)() as session:
            assert isinstance(session, AsyncSession)
            assert session.bind is engine
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


def test_startup_succeeds_and_shutdown_releases_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original_aclose = Redis.aclose
    original_dispose = AsyncEngine.dispose

    async def spy_aclose(self: Redis, *args: Any, **kwargs: Any) -> None:
        calls.append("redis.aclose")
        await original_aclose(self, *args, **kwargs)

    async def spy_dispose(self: AsyncEngine, *args: Any, **kwargs: Any) -> None:
        calls.append("engine.dispose")
        await original_dispose(self, *args, **kwargs)

    monkeypatch.setattr(Redis, "aclose", spy_aclose)
    monkeypatch.setattr(AsyncEngine, "dispose", spy_dispose)

    app = create_app(make_settings())
    with make_client(app) as client:
        assert isinstance(app.state.engine, AsyncEngine)
        assert isinstance(app.state.redis, Redis)
        assert client.get("/api/v1/health/live").status_code == 200
        assert calls == []

    assert calls == ["redis.aclose", "engine.dispose"]


def test_engine_is_disposed_even_if_redis_close_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed: list[bool] = []
    original_dispose = AsyncEngine.dispose

    async def failing_aclose(self: Redis, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("close failed")

    async def spy_dispose(self: AsyncEngine, *args: Any, **kwargs: Any) -> None:
        disposed.append(True)
        await original_dispose(self, *args, **kwargs)

    monkeypatch.setattr(Redis, "aclose", failing_aclose)
    monkeypatch.setattr(AsyncEngine, "dispose", spy_dispose)

    with pytest.raises(ConnectionError), make_client(create_app(make_settings())):
        pass

    assert disposed == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="ProactorEventLoop exists only on Windows")
def test_proactor_event_loop_is_rejected_with_clear_message() -> None:
    async def check() -> None:
        ensure_psycopg_compatible_event_loop()

    with pytest.raises(RuntimeError, match="SelectorEventLoop"):
        asyncio.run(check(), loop_factory=asyncio.ProactorEventLoop)
