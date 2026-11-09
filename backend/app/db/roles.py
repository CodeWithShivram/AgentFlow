"""Database roles (ADR-0010, OD-13).

- POSTGRES_USER runs Alembic and owns the schema. It is a superuser today; under OD-13 it
  becomes a non-superuser owner.
- The API also uses POSTGRES_USER today; OD-13 replaces it with a restricted `agentflow_api`.
- `agentflow_worker` (implemented now) is used by Celery workers: NOSUPERUSER, NOBYPASSRLS,
  owns nothing, explicit per-table grants only, and no access to `users`.

Provisioning is idempotent and must be re-run after migrations that add tables a worker needs:

    uv run python -m app.db.roles provision-worker
"""

import re
import sys

import psycopg
from psycopg import sql

from app.core.config import Settings, get_settings

# Least privilege for worker operations: dispatch/execution, outbox relay, inbox, cleanup.
# Locking rows (SELECT ... FOR UPDATE) requires UPDATE on the table.
WORKER_GRANTS: dict[str, tuple[str, ...]] = {
    "workflows": ("SELECT", "UPDATE"),
    "workflow_tasks": ("SELECT", "UPDATE"),
    "task_dependencies": ("SELECT",),
    "workflow_events": ("SELECT", "INSERT"),
    "agents": ("SELECT",),
    "agent_versions": ("SELECT",),
    "agent_executions": ("SELECT", "INSERT", "UPDATE"),
    "outbox_events": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "inbox_messages": ("SELECT", "INSERT", "DELETE"),
    "idempotency_keys": ("SELECT", "DELETE"),
    "auth_sessions": ("SELECT", "DELETE"),
    "refresh_tokens": ("SELECT", "DELETE"),
    "workspace_invitations": ("SELECT", "DELETE"),
}
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def provision_worker_role(connection: psycopg.Connection, *, role: str, password: str) -> None:
    """Creates or updates the worker role and resets its privileges to exactly WORKER_GRANTS
    in the connected database. Run as the schema owner / an administrator."""
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError("invalid role name")
    ident = sql.Identifier(role)
    attributes = sql.SQL(
        "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD {}"
    ).format(sql.Literal(password))
    with connection.transaction():
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
        connection.execute(sql.SQL("{} {} {}").format(verb, ident, attributes))
        database = connection.execute("SELECT current_database()").fetchone()
        if database is None:  # current_database() always returns one row
            raise RuntimeError("could not determine the current database")
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database[0]), ident)
        )
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident)
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(ident)
        )
        for table, privileges in WORKER_GRANTS.items():
            connection.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(p) for p in privileges),
                    sql.Identifier(table),
                    ident,
                )
            )


def admin_connection(settings: Settings, database: str | None = None) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=database or settings.postgres_db,
        connect_timeout=settings.db_connect_timeout_seconds,
    )


def _main(argv: list[str]) -> int:
    if argv != ["provision-worker"]:
        sys.stderr.write("usage: python -m app.db.roles provision-worker\n")
        return 2
    settings = get_settings()
    if settings.worker_db_password is None:
        sys.stderr.write("WORKER_DB_PASSWORD is not set in the environment or .env\n")
        return 1
    with admin_connection(settings) as connection:
        provision_worker_role(
            connection,
            role=settings.worker_db_user,
            password=settings.worker_db_password.get_secret_value(),
        )
    # Command-line output for the operator, not application logging.
    print(f"worker role {settings.worker_db_user!r} provisioned in {settings.postgres_db!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
