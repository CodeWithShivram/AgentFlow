"""plan drafts

Adds plan generation state to workflows, immutable plan drafts (`workflow_plans`, under
row-level security) and three workflow event types (Phase 2, Step 2.3, ADR-0015).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"

EVENT_TYPES_0008 = (
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
)
PLAN_EVENT_TYPES = ("PLAN_REQUESTED", "PLAN_GENERATED", "PLAN_GENERATION_FAILED")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _event_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", values)
    )


def upgrade() -> None:
    op.create_table(
        "workflow_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "task_count BETWEEN 1 AND 25", name=op.f("ck_workflow_plans_task_count_range")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_workflow_plans_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_workflow_plans_workflow",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_plans")),
        sa.UniqueConstraint(
            "workflow_id", "version", name=op.f("uq_workflow_plans_workflow_id_version")
        ),
    )
    # Drafts are immutable: a new generation is a new version.
    op.execute(
        """
        CREATE FUNCTION workflow_plans_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'workflow_plans are immutable; generate a new draft instead'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER workflow_plans_immutable BEFORE UPDATE ON workflow_plans "
        "FOR EACH ROW EXECUTE FUNCTION workflow_plans_reject_update()"
    )
    op.execute("ALTER TABLE workflow_plans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_plans FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY workflow_plans_tenant ON workflow_plans FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY workflow_plans_platform ON workflow_plans FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )

    op.add_column(
        "workflows",
        sa.Column(
            "plan_status",
            sa.Enum(
                "NOT_REQUESTED",
                "PENDING",
                "READY",
                "FAILED",
                name="plan_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'NOT_REQUESTED'"),
            nullable=False,
        ),
    )
    op.add_column("workflows", sa.Column("plan_request_id", sa.Uuid(), nullable=True))
    # The longest new type (22 characters) fits the existing VARCHAR(23).
    _event_type_check((*EVENT_TYPES_0008, *PLAN_EVENT_TYPES))


def downgrade() -> None:
    # workflow_events is append-only, so events of the new types are deleted, not rewritten.
    op.execute(
        "DELETE FROM workflow_events "
        "WHERE event_type IN ('PLAN_REQUESTED', 'PLAN_GENERATED', 'PLAN_GENERATION_FAILED')"
    )
    _event_type_check(EVENT_TYPES_0008)
    op.drop_column("workflows", "plan_request_id")
    op.drop_column("workflows", "plan_status")
    op.drop_table("workflow_plans")
    op.execute("DROP FUNCTION workflow_plans_reject_update()")
