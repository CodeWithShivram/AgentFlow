"""Async SQLAlchemy engine, session factory and request-scoped session dependency."""

import asyncio
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import URL, Connection, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction
from starlette.requests import HTTPConnection

from app.core.config import Settings

# Row-level security context (ADR-0012). `is_local = true` scopes the value to the current
# transaction, so it is gone after COMMIT or ROLLBACK and never reaches the next user of a
# pooled connection. Without it, policies match no workspace rows.
_RLS_USER_KEY = "rls_user_id"
_SET_RLS_USER = text("SELECT set_config('app.user_id', :user_id, true)")


def create_engine(settings: Settings, url: URL | None = None) -> AsyncEngine:
    """The API engine, connected as the restricted API role unless `url` says otherwise
    (seeding and tests pass the administrator URL). No connection is made here."""
    return create_async_engine(
        url or settings.api_database_url,
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


class TenantSession(Session):
    """The sync session behind the API's AsyncSession. Every transaction it begins carries the
    authenticated user for row-level security, once `set_session_user` has named one."""


@event.listens_for(TenantSession, "after_begin")
def _scope_transaction_to_user(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    user_id: Any = session.info.get(_RLS_USER_KEY)
    if user_id is not None:
        connection.execute(_SET_RLS_USER, {"user_id": user_id})


async def set_session_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Scopes the current and every later transaction of this session to `user_id`."""
    session.info[_RLS_USER_KEY] = str(user_id)
    await session.execute(_SET_RLS_USER, {"user_id": str(user_id)})


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine, sync_session_class=TenantSession, expire_on_commit=False, autoflush=False
    )


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
