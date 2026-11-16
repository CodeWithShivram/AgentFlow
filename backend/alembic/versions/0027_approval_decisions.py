"""approval decisions

Adds `approval_decisions`, the immutable audit record of every decision on an approval request
(who, in which role, what exactly, for which attempt), under row-level security with a trigger
that refuses updates, and `human_interventions.required_role`, the least role that may decide an
approval (Phase 6, Step 6.4, ADR-0038). Existing approvals are backfilled: OWNER when the asking
agent version grants a HIGH-risk tool (python_analysis, test_runner at this revision), otherwise
ADMIN. Decisions made before this migration have no audit record.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-16 12:40:15.953200+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("intervention_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("then", sa.String(length=8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("required_role", sa.String(length=16), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_role", sa.String(length=16), nullable=True),
        sa.Column("expired", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("action_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')", name=op.f("ck_approval_decisions_decision_known")
        ),
        sa.CheckConstraint(
            "(decided_by_user_id IS NULL) = (decided_role IS NULL)",
            name=op.f("ck_approval_decisions_role_with_person"),
        ),
        sa.CheckConstraint(
            "expired = (decided_by_user_id IS NULL)",
            name=op.f("ck_approval_decisions_person_or_expiry"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_approval_decisions_decided_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_approval_decisions_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["intervention_id"],
            ["human_interventions.id"],
            name=op.f("fk_approval_decisions_intervention_id_human_interventions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_approval_decisions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_approval_decisions_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_approval_decisions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_decisions")),
        sa.UniqueConstraint("intervention_id", name=op.f("uq_approval_decisions_intervention_id")),
    )
    op.create_index(
        "ix_approval_decisions_workspace_decided",
        "approval_decisions",
        ["workspace_id", "decided_at"],
        unique=False,
    )
    op.add_column(
        "human_interventions", sa.Column("required_role", sa.String(length=16), nullable=True)
    )
    # The catalog's HIGH-risk tools at this revision (app/agents/catalog.py).
    op.execute(
        """
        UPDATE human_interventions AS h
        SET required_role = CASE WHEN EXISTS (
                SELECT 1
                FROM agent_executions AS e
                JOIN agent_versions AS v ON v.id = e.agent_version_id
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(v.configuration -> 'tools', '[]'::jsonb)
                ) AS t(grant_)
                WHERE e.id::text = h.context ->> 'execution_id'
                  AND t.grant_ ->> 'tool' IN ('python_analysis', 'test_runner')
            ) THEN 'OWNER' ELSE 'ADMIN' END
        WHERE h.type = 'APPROVAL'
        """
    )
    op.create_check_constraint(
        op.f("ck_human_interventions_approval_has_required_role"),
        "human_interventions",
        "(type = 'APPROVAL') = (required_role IS NOT NULL)",
    )

    # Append-only: approval decisions are an audit record.
    op.execute(
        """
        CREATE FUNCTION approval_decisions_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval_decisions rows are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER approval_decisions_immutable BEFORE UPDATE ON approval_decisions "
        "FOR EACH ROW EXECUTE FUNCTION approval_decisions_reject_update()"
    )
    op.execute("ALTER TABLE approval_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY approval_decisions_tenant ON approval_decisions FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY approval_decisions_platform ON approval_decisions FOR ALL "
        f"TO {PLATFORM} USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY approval_decisions_platform ON approval_decisions")
    op.execute("DROP POLICY approval_decisions_tenant ON approval_decisions")
    op.execute("DROP TRIGGER approval_decisions_immutable ON approval_decisions")
    op.execute("DROP FUNCTION approval_decisions_reject_update()")
    op.drop_constraint(
        op.f("ck_human_interventions_approval_has_required_role"),
        "human_interventions",
        type_="check",
    )
    op.drop_column("human_interventions", "required_role")
    op.drop_index("ix_approval_decisions_workspace_decided", table_name="approval_decisions")
    op.drop_table("approval_decisions")
