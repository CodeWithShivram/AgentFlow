"""row level security

Enables PostgreSQL row-level security on every workspace-owned table (OD-13, ADR-0012) and
makes agent names unique case-insensitively within a workspace (OD-15).

Policies are written for two NOLOGIN group roles, which hold no privileges:
- agentflow_rls_tenant (the API role): rows of the workspaces that the user named by the
  transaction-local setting `app.user_id` belongs to, plus built-in agents (read-only);
- agentflow_rls_platform (Celery workers): every row; workers are limited by table grants.
Any other role that is not a superuser sees no rows. users, auth_sessions, refresh_tokens,
workspace_invitations (looked up by token before membership exists) and inbox_messages stay
outside RLS and are guarded by the service layer, as designed in ADR-0007.

The group roles are cluster-wide and may be used by other databases, so downgrade keeps them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"

# Table -> (tenant USING / WITH CHECK expression, extra read-only expression or None).
SCOPED_TABLES: dict[str, tuple[str, str | None]] = {
    # A workspace's owner sees it before the OWNER membership row exists (registration).
    "workspaces": (
        "id IN (SELECT public.app_user_workspace_ids()) OR owner_id = public.app_current_user_id()",
        None,
    ),
    "workflows": (IN_USER_WORKSPACES, None),
    "workflow_tasks": (IN_USER_WORKSPACES, None),
    "task_dependencies": (
        "workflow_id IN (SELECT id FROM public.workflows WHERE "
        "workspace_id IN (SELECT public.app_user_workspace_ids()))",
        None,
    ),
    "workflow_events": (IN_USER_WORKSPACES, None),
    "agents": (IN_USER_WORKSPACES, "workspace_id IS NULL"),
    "agent_versions": (
        "agent_id IN (SELECT id FROM public.agents WHERE "
        "workspace_id IN (SELECT public.app_user_workspace_ids()))",
        "agent_id IN (SELECT id FROM public.agents WHERE workspace_id IS NULL)",
    ),
    "agent_proposals": (IN_USER_WORKSPACES, None),
    "agent_audit_events": (IN_USER_WORKSPACES, None),
    "idempotency_keys": (IN_USER_WORKSPACES, None),
    "outbox_events": (IN_USER_WORKSPACES, None),
    "agent_executions": (IN_USER_WORKSPACES, None),
}

FUNCTIONS = """
CREATE FUNCTION public.app_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

-- SECURITY DEFINER so policies can read memberships without recursing into the
-- workspace_members policy. The pinned search_path stops lookups being redirected.
CREATE FUNCTION public.app_user_workspace_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
        SELECT workspace_id FROM public.workspace_members
        WHERE user_id = public.app_current_user_id()
    $$;

-- May the current user add themselves to `target`? Only as its owner (registration) or with
-- an unexpired invitation addressed to their email (acceptance).
CREATE FUNCTION public.app_can_join_workspace(target uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM public.workspaces w
            WHERE w.id = target AND w.owner_id = public.app_current_user_id()
        ) OR EXISTS (
            SELECT 1
            FROM public.workspace_invitations i
            JOIN public.users u ON u.id = public.app_current_user_id()
            WHERE i.workspace_id = target
              AND i.email = lower(u.email)
              AND i.expires_at > now()
              AND (i.accepted_at IS NULL OR i.accepted_by_user_id = u.id)
        )
    $$;
"""


def _create_group_roles() -> None:
    for role in (TENANT, PLATFORM):
        op.execute(
            f"""
            DO $$ BEGIN
                CREATE ROLE {role} NOLOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )


def _enable(table: str, tenant: str, read_only: str | None) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} FOR ALL TO {TENANT} "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    if read_only is not None:
        op.execute(
            f"CREATE POLICY {table}_tenant_read ON {table} FOR SELECT TO {TENANT} "
            f"USING ({read_only})"
        )
    op.execute(
        f"CREATE POLICY {table}_platform ON {table} FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )


def upgrade() -> None:
    _create_group_roles()
    op.execute(FUNCTIONS)
    for table, (tenant, read_only) in SCOPED_TABLES.items():
        _enable(table, tenant, read_only)

    # Members see their workspaces' memberships and always their own rows (which also lets a
    # freshly inserted membership pass the RETURNING check). Joining needs app_can_join_workspace.
    op.execute("ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_members FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY workspace_members_tenant ON workspace_members FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES} OR user_id = public.app_current_user_id()) "
        f"WITH CHECK ({IN_USER_WORKSPACES} OR (user_id = public.app_current_user_id() "
        "AND public.app_can_join_workspace(workspace_id)))"
    )
    op.execute(
        f"CREATE POLICY workspace_members_platform ON workspace_members FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )

    # OD-15: agent names are unique per workspace regardless of case (built-ins share NULL).
    op.drop_constraint("uq_agents_workspace_id_name", "agents", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX uq_agents_workspace_id_lower_name "
        "ON agents (workspace_id, lower(name)) NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_agents_workspace_id_lower_name")
    op.create_unique_constraint(
        "uq_agents_workspace_id_name",
        "agents",
        ["workspace_id", "name"],
        postgresql_nulls_not_distinct=True,
    )
    for table in [*SCOPED_TABLES, "workspace_members"]:
        for suffix in ("tenant", "tenant_read", "platform"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION public.app_can_join_workspace(uuid)")
    op.execute("DROP FUNCTION public.app_user_workspace_ids()")
    op.execute("DROP FUNCTION public.app_current_user_id()")
