"""agent selections

Adds `agent_selections`, one append-only row per assignment recording why the agent was chosen
by the rule-based selection (Phase 3, Step 3.6, ADR-0023), under row-level security (ADR-0012).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14 06:22:29.619864+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"


def upgrade() -> None:
    op.create_table(
        "agent_selections",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=1), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("top_agent_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(rank IS NULL) = (NOT eligible)", name=op.f("ck_agent_selections_rank_iff_eligible")
        ),
        sa.CheckConstraint(
            "candidate_count >= 1", name=op.f("ck_agent_selections_candidate_count_positive")
        ),
        sa.CheckConstraint(
            "rank IS NULL OR rank >= 1", name=op.f("ck_agent_selections_rank_positive")
        ),
        sa.CheckConstraint("score >= 0", name=op.f("ck_agent_selections_score_non_negative")),
        sa.ForeignKeyConstraint(
            ["agent_id", "agent_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            name="fk_agent_selections_agent_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_selections_decided_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_agent_selections_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_agent_selections_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agent_selections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_selections")),
    )
    op.create_index(
        "ix_agent_selections_task_id_created_at", "agent_selections", ["task_id", "created_at"]
    )

    # Append-only: rationales are history.
    op.execute(
        """
        CREATE FUNCTION agent_selections_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'agent_selections rows are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER agent_selections_immutable BEFORE UPDATE ON agent_selections "
        "FOR EACH ROW EXECUTE FUNCTION agent_selections_reject_update()"
    )

    op.execute("ALTER TABLE agent_selections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_selections FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY agent_selections_tenant ON agent_selections FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY agent_selections_platform ON agent_selections FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY agent_selections_platform ON agent_selections")
    op.execute("DROP POLICY agent_selections_tenant ON agent_selections")
    op.execute("DROP TRIGGER agent_selections_immutable ON agent_selections")
    op.execute("DROP FUNCTION agent_selections_reject_update()")
    op.drop_index("ix_agent_selections_task_id_created_at", table_name="agent_selections")
    op.drop_table("agent_selections")
