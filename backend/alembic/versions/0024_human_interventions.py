"""human interventions

Adds `human_interventions`: requests that need a person (clarifications, approvals, manual
recovery), under row-level security, and the INTERVENTION_OPENED and INTERVENTION_RESOLVED workflow
events (Phase 6, Step 6.1, ADR-0035).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-16 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"

EVENT_TYPES_0017 = (
    "WORKFLOW_CREATED",
    "PLAN_CREATED",
    "TASK_CREATED",
    "TASK_ASSIGNED",
    "TASK_QUEUED",
    "TASK_STARTED",
    "AGENT_SUBMITTED",
    "VERIFICATION_STARTED",
    "VERIFICATION_PASSED",
    "VERIFICATION_FAILED",
    "RETRY_REQUESTED",
    "TASK_REASSIGNED",
    "APPROVAL_REQUESTED",
    "APPROVAL_RESOLVED",
    "TASK_COMPLETED",
    "WORKFLOW_COMPLETED",
    "WORKFLOW_FAILED",
    "WORKFLOW_CANCELLED",
    "WORKFLOW_STATUS_CHANGED",
    "TASK_STATUS_CHANGED",
    "TASK_UPDATED",
    "TASK_REMOVED",
    "DEPENDENCY_ADDED",
    "DEPENDENCY_REMOVED",
    "ANALYSIS_REQUESTED",
    "REQUIREMENTS_ANALYZED",
    "ANALYSIS_FAILED",
    "PLAN_REQUESTED",
    "PLAN_GENERATED",
    "PLAN_GENERATION_FAILED",
    "PLAN_CONFIRMED",
    "BACKGROUND_JOB_RETRIED",
)
NEW_EVENT_TYPES = ("INTERVENTION_OPENED", "INTERVENTION_RESOLVED")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _event_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", values)
    )


def upgrade() -> None:
    op.create_table(
        "human_interventions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "CLARIFICATION",
                "APPROVAL",
                "REJECTION",
                "MANUAL_RECOVERY",
                name="type",
                native_enum=False,
                create_constraint=True,
                length=15,
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "RESOLVED",
                "WITHDRAWN",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),
            nullable=False,
        ),
        sa.Column("requested_action", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(length=16), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'OPEN') = (resolved_at IS NULL)",
            name=op.f("ck_human_interventions_open_iff_unresolved"),
        ),
        sa.CheckConstraint(
            "requested_by IN ('SYSTEM', 'AGENT', 'USER')",
            name=op.f("ck_human_interventions_requested_by_known"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_agent_id"],
            ["agents.id"],
            name=op.f("fk_human_interventions_requested_by_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_human_interventions_requested_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name=op.f("fk_human_interventions_resolved_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_human_interventions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_human_interventions_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_human_interventions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_human_interventions")),
        sa.UniqueConstraint(
            "workflow_id", "source_key", name=op.f("uq_human_interventions_workflow_id_source_key")
        ),
    )
    op.create_index(
        "ix_human_interventions_workspace_status",
        "human_interventions",
        ["workspace_id", "status", "created_at"],
    )
    op.execute("ALTER TABLE human_interventions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE human_interventions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY human_interventions_tenant ON human_interventions FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY human_interventions_platform ON human_interventions FOR ALL "
        f"TO {PLATFORM} USING (true) WITH CHECK (true)"
    )
    _event_type_check((*EVENT_TYPES_0017, *NEW_EVENT_TYPES))


def downgrade() -> None:
    # workflow_events is append-only, so events of the new types are deleted, not rewritten.
    op.execute(
        "DELETE FROM workflow_events WHERE event_type IN "
        "('INTERVENTION_OPENED', 'INTERVENTION_RESOLVED')"
    )
    _event_type_check(EVENT_TYPES_0017)
    op.execute("DROP POLICY human_interventions_platform ON human_interventions")
    op.execute("DROP POLICY human_interventions_tenant ON human_interventions")
    op.drop_index("ix_human_interventions_workspace_status", table_name="human_interventions")
    op.drop_table("human_interventions")
