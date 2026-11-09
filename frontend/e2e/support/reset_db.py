"""Drops and recreates the disposable E2E database. Run from backend/ with `uv run python`.

Refuses any database whose name does not end in `_e2e`, so it can never touch the dev database.
"""

import psycopg
from psycopg import sql

from app.core.config import Settings

settings = Settings()
name = settings.postgres_db
if not name.endswith("_e2e"):
    raise SystemExit(f"refusing to reset {name!r}: only *_e2e databases are disposable")

with psycopg.connect(
    host=settings.postgres_host,
    port=settings.postgres_port,
    user=settings.postgres_user,
    password=settings.postgres_password.get_secret_value(),
    dbname="postgres",
    autocommit=True,
    connect_timeout=5,
) as admin:
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
print(f"reset {name}")
