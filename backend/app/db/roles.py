"""Database roles (ADR-0010, ADR-0012).

- POSTGRES_USER is the administrator: it runs Alembic, owns the schema, seeds built-in agents
  and provisions the roles below. Applications never connect as it.
- `agentflow_api` (API_DB_USER) is used by the API: NOSUPERUSER, NOBYPASSRLS, owns nothing,
  data privileges on application tables only. Row-level security limits it to the workspaces
  of the user named in `app.user_id` (group role `agentflow_rls_tenant`).
- `agentflow_worker` (WORKER_DB_USER) is used by Celery workers: explicit per-table grants
  only, and no access to `users`. Relaying and cleanup are cross-tenant by nature, so its
  policies (group role `agentflow_rls_platform`) are not workspace-scoped.

Both group roles are NOLOGIN and hold no privileges; migration 0006 creates them and writes
the policies against them. Provisioning is idempotent and must be re-run after migrations
that add tables:

    uv run python -m app.db.roles provision
"""

import re
import sys

import psycopg
from psycopg import sql

from app.core.config import Settings, get_settings

TENANT_GROUP = "agentflow_rls_tenant"
PLATFORM_GROUP = "agentflow_rls_platform"

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
API_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
# Tables the API never touches: migration bookkeeping and worker-only state.
API_EXCLUDED_TABLES = frozenset({"alembic_version", "inbox_messages"})
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_LOGIN_ATTRIBUTES = (
    "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD {}"
)


def _ensure_group_roles(connection: psycopg.Connection) -> None:
    for group in (TENANT_GROUP, PLATFORM_GROUP):
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (group,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(group)))


def _reset_login_role(
    connection: psycopg.Connection, *, role: str, password: str, group: str
) -> sql.Identifier:
    """Creates or updates a restricted login role, clears its table privileges in the connected
    database and makes it a member of exactly one RLS group role."""
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError("invalid role name")
    ident = sql.Identifier(role)
    _ensure_group_roles(connection)
    exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
    connection.execute(
        sql.SQL("{} {} {}").format(
            verb, ident, sql.SQL(_LOGIN_ATTRIBUTES).format(sql.Literal(password))
        )
    )
    database = connection.execute("SELECT current_database()").fetchone()
    if database is None:  # current_database() always returns one row
        raise RuntimeError("could not determine the current database")
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database[0]), ident)
    )
    connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
    connection.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident))
    connection.execute(
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(ident)
    )
    other = PLATFORM_GROUP if group == TENANT_GROUP else TENANT_GROUP
    connection.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(other), ident))
    # Explicit INHERIT TRUE: policies written for the group must apply although the login role
    # itself is NOINHERIT. The groups hold no privileges, so nothing else is inherited.
    connection.execute(
        sql.SQL("GRANT {} TO {} WITH INHERIT TRUE").format(sql.Identifier(group), ident)
    )
    return ident


def provision_worker_role(connection: psycopg.Connection, *, role: str, password: str) -> None:
    """Creates or updates the worker role and resets its privileges to exactly WORKER_GRANTS
    in the connected database. Run as the administrator."""
    with connection.transaction():
        ident = _reset_login_role(connection, role=role, password=password, group=PLATFORM_GROUP)
        for table, privileges in WORKER_GRANTS.items():
            connection.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(p) for p in privileges),
                    sql.Identifier(table),
                    ident,
                )
            )


def provision_api_role(connection: psycopg.Connection, *, role: str, password: str) -> None:
    """Creates or updates the API role: data privileges on every application table (row-level
    security does the scoping), sequence usage, no ownership. Run as the administrator."""
    with connection.transaction():
        ident = _reset_login_role(connection, role=role, password=password, group=TENANT_GROUP)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            ).fetchall()
            if row[0] not in API_EXCLUDED_TABLES
        ]
        for table in tables:
            connection.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(p) for p in API_PRIVILEGES),
                    sql.Identifier(table),
                    ident,
                )
            )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(ident)
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


_USAGE = "usage: python -m app.db.roles provision | provision-api | provision-worker\n"


def _main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"provision", "provision-api", "provision-worker"}:
        sys.stderr.write(_USAGE)
        return 2
    command = argv[0]
    settings = get_settings()
    wanted = {
        "api": command in {"provision", "provision-api"},
        "worker": command in {"provision", "provision-worker"},
    }
    if wanted["api"] and settings.api_db_password is None:
        sys.stderr.write("API_DB_PASSWORD is not set in the environment or .env\n")
        return 1
    if wanted["worker"] and settings.worker_db_password is None:
        sys.stderr.write("WORKER_DB_PASSWORD is not set in the environment or .env\n")
        return 1
    with admin_connection(settings) as connection:
        if wanted["api"] and settings.api_db_password is not None:
            provision_api_role(
                connection,
                role=settings.api_db_user,
                password=settings.api_db_password.get_secret_value(),
            )
            # Command-line output for the operator, not application logging.
            print(f"api role {settings.api_db_user!r} provisioned in {settings.postgres_db!r}")
        if wanted["worker"] and settings.worker_db_password is not None:
            provision_worker_role(
                connection,
                role=settings.worker_db_user,
                password=settings.worker_db_password.get_secret_value(),
            )
            print(
                f"worker role {settings.worker_db_user!r} provisioned in {settings.postgres_db!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
