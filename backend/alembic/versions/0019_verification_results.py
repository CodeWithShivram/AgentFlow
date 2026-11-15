"""verification results

Adds `verification_results`, one append-only row per verifier per attempt recording the checks
performed on a submission, the issues found and the outcome (Phase 5, Step 5.1, ADR-0030), under
row-level security (ADR-0012).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-15 08:10:08.130149+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"


def upgrade() -> None:
    op.create_table(
        "verification_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("verifier", sa.String(length=32), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "PASSED",
                "FAILED",
                name="outcome",
                native_enum=False,
                create_constraint=True,
                length=6,
            ),
            nullable=False,
        ),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_verification_results_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_verification_results_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_verification_results_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_verification_results_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_results")),
        sa.UniqueConstraint(
            "execution_id",
            "verifier",
            name=op.f("uq_verification_results_execution_id_verifier"),
        ),
    )
    op.create_index(
        "ix_verification_results_task_id_created_at",
        "verification_results",
        ["task_id", "created_at"],
    )

    # Append-only: verification results are history.
    op.execute(
        """
        CREATE FUNCTION verification_results_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'verification_results rows are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER verification_results_immutable BEFORE UPDATE ON verification_results "
        "FOR EACH ROW EXECUTE FUNCTION verification_results_reject_update()"
    )

    op.execute("ALTER TABLE verification_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verification_results FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY verification_results_tenant ON verification_results FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY verification_results_platform ON verification_results FOR ALL "
        f"TO {PLATFORM} USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY verification_results_platform ON verification_results")
    op.execute("DROP POLICY verification_results_tenant ON verification_results")
    op.execute("DROP TRIGGER verification_results_immutable ON verification_results")
    op.execute("DROP FUNCTION verification_results_reject_update()")
    op.drop_index("ix_verification_results_task_id_created_at", table_name="verification_results")
    op.drop_table("verification_results")
