"""Alembic environment.

Migrations run synchronously with psycopg 3 (the same driver the async API uses), which
avoids event-loop constraints on Windows. The URL comes from Settings unless a caller (tests)
passes `config.attributes["database_url"]`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, Connection, create_engine, pool

from app.core.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> URL | str:
    override: URL | str | None = config.attributes.get("database_url")
    return override if override is not None else get_settings().database_url


def _configure_and_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            _configure_and_run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
