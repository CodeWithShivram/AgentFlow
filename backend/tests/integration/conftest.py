"""Disposable PostgreSQL databases for migration and schema tests.

Each database is created on the local server under a random name and dropped afterwards, so
the development database is never touched.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Base

INFRA_HINT = (
    "Integration tests need local PostgreSQL and Redis and the repo-root .env. "
    "Run: powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 up "
    '(or skip them with: uv run pytest -m "not integration").'
)
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _admin_connection(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname="postgres",
        autocommit=True,
        connect_timeout=5,
    )


@contextmanager
def disposable_database() -> Iterator[URL]:
    """Creates an empty database and yields its URL; drops it on exit."""
    try:
        settings = Settings(app_env="test")
        admin = _admin_connection(settings)
    except (ValueError, psycopg.Error) as exc:
        pytest.fail(f"{INFRA_HINT} ({type(exc).__name__})", pytrace=False)
    name = f"agentflow_test_{uuid.uuid4().hex[:12]}"
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield settings.database_url.set(database=name)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def alembic_config(database_url: URL) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[URL]:
    """One database migrated to head, shared by the schema and idempotency tests."""
    with disposable_database() as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture
async def engine(migrated_database_url: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url, pool_size=10)
    try:
        yield engine
    finally:
        # Leave the shared database empty for the next test.
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
        await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose transaction is rolled back after the test."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
        finally:
            await session.rollback()
