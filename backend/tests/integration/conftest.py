"""Disposable PostgreSQL databases for migration and schema tests.

Each database is created on the local server under a random name and dropped afterwards, so
the development database is never touched.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, WebSocket
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import URL, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.api.deps import Principal, get_websocket_principal
from app.core.config import Settings
from app.main import create_app
from app.models import Base
from tests.conftest import ALLOWED_ORIGIN, UNIT_JWT_KEY, UNIT_JWT_KEY_ID, make_client
from tests.integration.api_helpers import ClientFactory

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


def _truncate_all(database_url: URL) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    sync_engine = create_engine(database_url)
    try:
        with sync_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    finally:
        sync_engine.dispose()


@pytest.fixture
def api_settings(migrated_database_url: URL) -> Iterator[Settings]:
    """Settings for an app bound to the migrated test database.

    Credentials come from the repo-root .env. A unique service name isolates rate-limit counters
    in the shared Redis. Tables are emptied after the test.
    """
    settings = Settings(
        app_env="test",
        postgres_db=migrated_database_url.database,
        service_name=f"agentflow-it-{uuid.uuid4().hex[:8]}",
        auth_jwt_signing_keys={UNIT_JWT_KEY_ID: UNIT_JWT_KEY},
        auth_jwt_active_key_id=UNIT_JWT_KEY_ID,
        cors_allowed_origins=[ALLOWED_ORIGIN],
    )
    try:
        yield settings
    finally:
        _truncate_all(migrated_database_url)


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


@pytest.fixture
def new_client(api_settings: Settings) -> Iterator[ClientFactory]:
    """Each client is a separate browser (its own cookie jar) against its own app instance."""
    with ExitStack() as stack:

        def factory(settings: Settings | None = None, *, raise_errors: bool = True) -> TestClient:
            app = create_app(settings or api_settings)

            @app.websocket("/test/ws")
            async def whoami(
                websocket: WebSocket,
                principal: Annotated[Principal, Depends(get_websocket_principal)],
            ) -> None:
                await websocket.accept()
                await websocket.send_text(str(principal.user_id))
                await websocket.close()

            return stack.enter_context(make_client(app, raise_server_exceptions=raise_errors))

        yield factory


@pytest.fixture
def client(new_client: ClientFactory) -> TestClient:
    return new_client()


@pytest.fixture
def seeded_builtins(api_settings: Settings) -> Settings:
    """Seeds the six built-in agents into the test database (emptied after each test)."""
    from app.agents.seed import seed_builtin_agents
    from app.db.session import create_engine as create_app_engine
    from tests.conftest import LOOP_FACTORY

    async def seed() -> None:
        engine = create_app_engine(api_settings)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await seed_builtin_agents(session)
        finally:
            await engine.dispose()

    asyncio.run(seed(), loop_factory=LOOP_FACTORY)
    return api_settings


# --- background execution -----------------------------------------------------------------------

WORKER_TEST_ROLE = "agentflow_worker_it"


@pytest.fixture(scope="session")
def worker_role(migrated_database_url: URL) -> Iterator[tuple[str, str]]:
    """The restricted worker role, provisioned in the shared test database."""
    from app.db.roles import admin_connection, provision_worker_role

    settings = Settings(app_env="test")
    password = uuid.uuid4().hex + uuid.uuid4().hex
    database = migrated_database_url.database
    with admin_connection(settings, database=database) as connection:
        provision_worker_role(connection, role=WORKER_TEST_ROLE, password=password)
    try:
        yield WORKER_TEST_ROLE, password
    finally:
        # Privileges live in the test database; drop them there, then the cluster-wide role.
        with admin_connection(settings, database=database) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(WORKER_TEST_ROLE)))
            connection.commit()
        with _admin_connection(settings) as admin:
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(WORKER_TEST_ROLE))
            )


@pytest.fixture
def worker_factory(
    migrated_database_url: URL, worker_role: tuple[str, str], api_settings: Settings
) -> Iterator[Any]:
    """Sync session factory connected as the restricted worker role (like Celery workers)."""
    from app.db.sync_session import create_worker_engine, create_worker_session_factory

    role, password = worker_role
    engine = create_worker_engine(
        api_settings, migrated_database_url.set(username=role, password=password)
    )
    try:
        yield create_worker_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def admin_factory(api_settings: Settings) -> Iterator[Any]:
    """Sync session factory as the schema owner, for test setup."""
    from app.db.sync_session import create_worker_engine, create_worker_session_factory

    engine = create_worker_engine(api_settings, api_settings.database_url)
    try:
        yield create_worker_session_factory(engine)
    finally:
        engine.dispose()
