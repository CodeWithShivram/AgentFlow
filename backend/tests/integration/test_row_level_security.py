"""Row-level security (OD-13, ADR-0012) enforced by PostgreSQL itself.

These tests talk to the database as the restricted API role with raw SQL that has no
`workspace_id` filter, so they prove isolation does not depend on the application's queries.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import Settings
from app.db.roles import API_EXCLUDED_TABLES, PLATFORM_GROUP, TENANT_GROUP
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    accept,
    csrf,
    invite,
    my_workspace,
    new_email,
    register,
    sql,
)

pytestmark = pytest.mark.integration

ROW_LEVEL_SECURED = {
    "workspaces",
    "workspace_members",
    "workflows",
    "workflow_tasks",
    "task_dependencies",
    "workflow_events",
    "agents",
    "agent_versions",
    "agent_proposals",
    "agent_audit_events",
    "idempotency_keys",
    "outbox_events",
    "agent_executions",
}
# Read before any workspace context exists (login, refresh, invitation by token) or worker-only.
OUTSIDE_RLS = {
    "users",
    "auth_sessions",
    "refresh_tokens",
    "workspace_invitations",
    "inbox_messages",
}


@pytest.fixture
def api_engine(api_settings: Settings) -> Iterator[Engine]:
    # One pooled connection, so the "no leftover context" checks reuse the same connection.
    engine = create_engine(api_settings.api_database_url, pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        engine.dispose()


def _user_id(client: TestClient) -> str:
    user_id: str = client.get(f"{API}/auth/me").json()["user"]["id"]
    return user_id


def _tenant(new_client: ClientFactory) -> dict[str, Any]:
    """A registered user with one workflow in their personal workspace."""
    client = new_client()
    assert register(client, new_email()).status_code == 201
    workspace = my_workspace(client)
    created = client.post(
        f"{API}/workspaces/{workspace}/workflows",
        json={"prompt": "secret plan"},
        headers=csrf(client),
    )
    assert created.status_code == 201
    return {
        "client": client,
        "user_id": _user_id(client),
        "workspace_id": workspace,
        "workflow_id": created.json()["id"],
    }


def _as_user(engine: Engine, user_id: str | None, statement: str, **params: Any) -> list[Any]:
    """Runs one statement as the API role in a transaction scoped to `user_id` (or none)."""
    with engine.begin() as connection:
        if user_id is not None:
            connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": user_id})
        result = connection.execute(text(statement), params)
        return list(result.all()) if result.returns_rows else [result.rowcount]


def test_rls_is_enabled_and_forced_on_exactly_the_workspace_tables(api_settings: Settings) -> None:
    rows = sql(
        api_settings,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'",
    )
    secured = {name for name, enabled, forced in rows if enabled and forced}
    assert secured == ROW_LEVEL_SECURED
    unsecured = {name for name, enabled, _ in rows if not enabled}
    assert OUTSIDE_RLS <= unsecured


def test_api_role_is_restricted(api_settings: Settings) -> None:
    role = api_settings.api_db_user
    attributes = sql(
        api_settings,
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
        "FROM pg_roles WHERE rolname = :r",
        r=role,
    )
    owned = sql(api_settings, "SELECT count(*) FROM pg_tables WHERE tableowner = :r", r=role)
    groups = sql(
        api_settings,
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = :r",
        r=role,
    )
    excluded = sql(
        api_settings,
        "SELECT table_name FROM information_schema.role_table_grants "
        "WHERE grantee = :r AND table_name = ANY(:t)",
        r=role,
        t=sorted(API_EXCLUDED_TABLES),
    )

    assert tuple(attributes[0]) == (False, False, False, False)
    assert owned[0][0] == 0
    assert {g for (g,) in groups} == {TENANT_GROUP}
    assert excluded == []


def test_tenants_cannot_read_each_others_rows_without_any_application_filter(
    new_client: ClientFactory, api_engine: Engine
) -> None:
    alice, bob = _tenant(new_client), _tenant(new_client)

    for table in ("workflows", "workflow_events", "outbox_events", "idempotency_keys"):
        # Table names come from the literal tuple above.
        query = f"SELECT DISTINCT workspace_id FROM {table}"  # noqa: S608
        rows = _as_user(api_engine, alice["user_id"], query)
        assert {str(w) for (w,) in rows} <= {alice["workspace_id"]}, table
    seen = _as_user(api_engine, alice["user_id"], "SELECT id FROM workflows")
    assert [str(w) for (w,) in seen] == [alice["workflow_id"]]
    workspaces = _as_user(api_engine, alice["user_id"], "SELECT id FROM workspaces")
    assert [str(w) for (w,) in workspaces] == [alice["workspace_id"]]
    members = _as_user(
        api_engine, alice["user_id"], "SELECT DISTINCT workspace_id FROM workspace_members"
    )
    assert [str(w) for (w,) in members] == [alice["workspace_id"]]
    assert bob["workflow_id"] not in {str(w) for (w,) in seen}


def test_tenants_cannot_write_into_or_change_other_workspaces(
    new_client: ClientFactory, api_engine: Engine
) -> None:
    alice, bob = _tenant(new_client), _tenant(new_client)

    with pytest.raises(DBAPIError, match="row-level security"):
        _as_user(
            api_engine,
            alice["user_id"],
            "INSERT INTO workflows "
            "(workspace_id, created_by_user_id, prompt, status, autonomy_policy) "
            "VALUES (:w, :u, 'injected', 'PENDING', 'approval_required')",
            w=bob["workspace_id"],
            u=alice["user_id"],
        )
    updated = _as_user(
        api_engine,
        alice["user_id"],
        "UPDATE workflows SET prompt = 'x' WHERE id = :id",
        id=bob["workflow_id"],
    )
    deleted = _as_user(
        api_engine, alice["user_id"], "DELETE FROM workflows WHERE id = :id", id=bob["workflow_id"]
    )
    assert updated == [0]
    assert deleted == [0]
    # Moving one's own row into another workspace is rejected too.
    with pytest.raises(DBAPIError, match="row-level security"):
        _as_user(
            api_engine,
            alice["user_id"],
            "UPDATE workflows SET workspace_id = :w WHERE id = :id",
            w=bob["workspace_id"],
            id=alice["workflow_id"],
        )
    assert (
        bob["client"]
        .get(f"{API}/workspaces/{bob['workspace_id']}/workflows/{bob['workflow_id']}")
        .json()["prompt"]
        == "secret plan"
    )


def test_without_a_user_context_no_workspace_rows_are_visible(
    new_client: ClientFactory, api_engine: Engine, seeded_builtins: Settings
) -> None:
    _tenant(new_client)

    assert _as_user(api_engine, None, "SELECT count(*) FROM workflows") == [(0,)]
    assert _as_user(api_engine, None, "SELECT count(*) FROM workspaces") == [(0,)]
    assert _as_user(api_engine, None, "SELECT count(*) FROM workspace_members") == [(0,)]
    # Built-in agents and their versions are shared, read-only platform data.
    assert _as_user(api_engine, None, "SELECT count(*) FROM agents") == [(6,)]
    assert _as_user(api_engine, None, "SELECT count(*) FROM agent_versions") == [(6,)]


def test_built_in_agents_are_read_only_for_the_api(
    new_client: ClientFactory, api_engine: Engine, seeded_builtins: Settings
) -> None:
    alice = _tenant(new_client)

    updated = _as_user(
        api_engine,
        alice["user_id"],
        "UPDATE agents SET description = 'x' WHERE workspace_id IS NULL",
    )
    deleted = _as_user(
        api_engine, alice["user_id"], "DELETE FROM agents WHERE workspace_id IS NULL"
    )
    assert updated == [0]
    assert deleted == [0]
    with pytest.raises(DBAPIError, match="row-level security"):
        _as_user(
            api_engine,
            alice["user_id"],
            "INSERT INTO agents (name, category, description, status, revision) "
            "VALUES ('Forged', 'general', '', 'INACTIVE', 1)",
        )


def test_context_never_outlives_its_transaction(
    new_client: ClientFactory, api_engine: Engine
) -> None:
    alice = _tenant(new_client)

    assert _as_user(api_engine, alice["user_id"], "SELECT count(*) FROM workflows") == [(1,)]
    # Same pooled connection, next transaction, no context: nothing remains.
    assert _as_user(api_engine, None, "SELECT current_setting('app.user_id', true)") in (
        [("",)],
        [(None,)],
    )
    assert _as_user(api_engine, None, "SELECT count(*) FROM workflows") == [(0,)]
    with pytest.raises(DBAPIError), api_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :u, true)"), {"u": alice["user_id"]}
        )
        connection.execute(text("SELECT 1/0"))
    assert _as_user(api_engine, None, "SELECT count(*) FROM workflows") == [(0,)]


def test_users_join_workspaces_only_as_owner_or_by_invitation(
    new_client: ClientFactory, api_engine: Engine
) -> None:
    alice, bob = _tenant(new_client), _tenant(new_client)

    with pytest.raises(DBAPIError, match="row-level security"):
        _as_user(
            api_engine,
            alice["user_id"],
            "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (:w, :u, 'VIEWER')",
            w=bob["workspace_id"],
            u=alice["user_id"],
        )

    # With an invitation addressed to Alice, the API flow works under RLS.
    alice_email = alice["client"].get(f"{API}/auth/me").json()["user"]["email"]
    token = invite(bob["client"], bob["workspace_id"], alice_email, "VIEWER").json()[
        "invitation_token"
    ]
    assert accept(alice["client"], token).status_code == 200
    visible = _as_user(api_engine, alice["user_id"], "SELECT id FROM workflows")
    assert {str(w) for (w,) in visible} == {alice["workflow_id"], bob["workflow_id"]}


def test_policies_target_only_the_group_roles(api_settings: Settings) -> None:
    roles = sql(
        api_settings,
        "SELECT DISTINCT unnest(roles)::text FROM pg_policies WHERE schemaname = 'public'",
    )
    assert {r for (r,) in roles} == {TENANT_GROUP, PLATFORM_GROUP}


def test_agent_names_are_unique_per_workspace_regardless_of_case(
    new_client: ClientFactory, api_settings: Settings
) -> None:
    alice = _tenant(new_client)
    insert = (
        "INSERT INTO agents (workspace_id, name, category, description, status, revision) "
        "VALUES (:w, :n, 'general', '', 'INACTIVE', 1)"
    )
    sql(api_settings, insert, w=alice["workspace_id"], n="Summariser")
    with pytest.raises(IntegrityError, match="uq_agents_workspace_id_lower_name"):
        sql(api_settings, insert, w=alice["workspace_id"], n="SUMMARISER")
    # Another workspace may reuse the name.
    sql(api_settings, insert, w=_tenant(new_client)["workspace_id"], n="summariser")
