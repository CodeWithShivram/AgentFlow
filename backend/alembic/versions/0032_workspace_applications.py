"""workspace applications

Adds `workspace_applications` (Step 9.3, ADR-0049): one append-only row per accepted software
project attempt applied to its workspace, APPLIED with its commit or FAILED with a fixed code.
Row-level security as for every workspace-owned table; workers insert, nobody updates.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-26 11:55:00.082350+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_applications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("APPLIED", "FAILED", name="status", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("files_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_removed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tree_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'APPLIED') = (error_code IS NULL AND head_sha IS NOT NULL)",
            name=op.f("ck_workspace_applications_applied_or_error"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_workspace_applications_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_workspace_applications_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_workspace_applications_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_applications_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_applications")),
        sa.UniqueConstraint("execution_id", name=op.f("uq_workspace_applications_execution_id")),
    )
    op.create_index(
        "ix_workspace_applications_workflow_created",
        "workspace_applications",
        ["workflow_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION workspace_applications_reject_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'workspace_applications rows are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER workspace_applications_immutable BEFORE UPDATE ON workspace_applications "
        "FOR EACH ROW EXECUTE FUNCTION workspace_applications_reject_update()"
    )
    op.execute("ALTER TABLE workspace_applications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_applications FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY workspace_applications_tenant ON workspace_applications FOR ALL "
        "TO agentflow_rls_tenant USING (workspace_id IN (SELECT public.app_user_workspace_ids())) "
        "WITH CHECK (workspace_id IN (SELECT public.app_user_workspace_ids()))"
    )
    op.execute(
        "CREATE POLICY workspace_applications_platform ON workspace_applications FOR ALL "
        "TO agentflow_rls_platform USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY workspace_applications_platform ON workspace_applications")
    op.execute("DROP POLICY workspace_applications_tenant ON workspace_applications")
    op.execute("DROP TRIGGER workspace_applications_immutable ON workspace_applications")
    op.execute("DROP FUNCTION workspace_applications_reject_update()")
    op.drop_index("ix_workspace_applications_workflow_created", table_name="workspace_applications")
    op.drop_table("workspace_applications")
