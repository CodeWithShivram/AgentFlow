"""project integrations

Adds `project_integrations` (Step 9.5, ADR-0051): each run of a software project's integration
gate on one workspace commit. A row is recorded RUNNING and finished once (PASSED, FAILED or
NOT_RUN); a trigger refuses any other change. Row-level security as for every workspace-owned
table.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-26 12:24:30.041876+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_integrations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("stack_profile", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "PASSED",
                "FAILED",
                "NOT_RUN",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'RUNNING') = (finished_at IS NULL)",
            name=op.f("ck_project_integrations_finished_unless_running"),
        ),
        sa.CheckConstraint(
            "head_sha ~ '^[0-9a-f]{40}$'", name=op.f("ck_project_integrations_head_sha_hex")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_project_integrations_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_project_integrations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_integrations")),
    )
    op.create_index(
        "ix_project_integrations_workflow_created",
        "project_integrations",
        ["workflow_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION project_integrations_guard_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'RUNNING'
               OR (NEW.id, NEW.workspace_id, NEW.workflow_id, NEW.head_sha, NEW.stack_profile,
                   NEW.created_at)
                  IS DISTINCT FROM
                  (OLD.id, OLD.workspace_id, OLD.workflow_id, OLD.head_sha, OLD.stack_profile,
                   OLD.created_at) THEN
                RAISE EXCEPTION 'project_integrations rows are finished once'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER project_integrations_guard BEFORE UPDATE ON project_integrations "
        "FOR EACH ROW EXECUTE FUNCTION project_integrations_guard_update()"
    )
    op.execute("ALTER TABLE project_integrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE project_integrations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY project_integrations_tenant ON project_integrations FOR ALL "
        "TO agentflow_rls_tenant USING (workspace_id IN (SELECT public.app_user_workspace_ids())) "
        "WITH CHECK (workspace_id IN (SELECT public.app_user_workspace_ids()))"
    )
    op.execute(
        "CREATE POLICY project_integrations_platform ON project_integrations FOR ALL "
        "TO agentflow_rls_platform USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY project_integrations_platform ON project_integrations")
    op.execute("DROP POLICY project_integrations_tenant ON project_integrations")
    op.execute("DROP TRIGGER project_integrations_guard ON project_integrations")
    op.execute("DROP FUNCTION project_integrations_guard_update()")
    op.drop_index("ix_project_integrations_workflow_created", table_name="project_integrations")
    op.drop_table("project_integrations")
