"""Async SQLAlchemy engine, session factory and request-scoped session dependency."""

import asyncio
import sys
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import HTTPConnection

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    # No connection is made here; an unavailable database never blocks startup.
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "application_name": settings.service_name,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def ensure_psycopg_compatible_event_loop() -> None:
    """Fail fast with a clear message instead of every query failing later."""
    if sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), asyncio.ProactorEventLoop
    ):
        raise RuntimeError(
            "Async psycopg cannot run on the Windows ProactorEventLoop. "
            "Start uvicorn with --loop asyncio:SelectorEventLoop."
        )


def get_engine(connection: HTTPConnection) -> AsyncEngine:
    engine: AsyncEngine = connection.app.state.engine
    return engine


async def get_db_session(connection: HTTPConnection) -> AsyncGenerator[AsyncSession]:
    """Yields one session per request or WebSocket. Rolls back on error; services own commits."""
    factory: async_sessionmaker[AsyncSession] = connection.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
