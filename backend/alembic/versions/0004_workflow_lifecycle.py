"""workflow lifecycle

Final workflow statuses (OD-11), autonomy policy values, revisions for optimistic concurrency,
required/removed task flags, dynamic-proposal opt-in and new workflow event types. ADR-0009.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_WORKFLOW_STATUSES = (
    "PENDING",
    "RUNNING",
    "WAITING_APPROVAL",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
NEW_WORKFLOW_STATUSES = (
    "PENDING",
    "PLANNING",
    "AWAITING_APPROVAL",
    "READY",
    "RUNNING",
    "PAUSED",
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
OLD_EVENT_TYPES = (
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
)
NEW_EVENT_TYPES = (
    *OLD_EVENT_TYPES,
    "WORKFLOW_STATUS_CHANGED",
    "TASK_STATUS_CHANGED",
    "TASK_UPDATED",
    "TASK_REMOVED",
    "DEPENDENCY_ADDED",
    "DEPENDENCY_REMOVED",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- workflows ---------------------------------------------------------------------------
    op.drop_constraint(op.f("ck_workflows_status"), "workflows", type_="check")
    op.alter_column("workflows", "status", type_=sa.String(length=17), existing_nullable=False)
    op.execute(
        "UPDATE workflows SET status = 'AWAITING_APPROVAL' WHERE status = 'WAITING_APPROVAL'"
    )
    op.create_check_constraint(
        op.f("ck_workflows_status"), "workflows", _in("status", NEW_WORKFLOW_STATUSES)
    )
    # Unknown legacy policies become the safe default before the CHECK is added.
    op.execute(
        "UPDATE workflows SET autonomy_policy = 'approval_required' "
        "WHERE autonomy_policy NOT IN ('approval_required', 'autonomous')"
    )
    op.alter_column(
        "workflows", "autonomy_policy", type_=sa.String(length=17), existing_nullable=False
    )
    op.create_check_constraint(
        op.f("ck_workflows_autonomy_policy"),
        "workflows",
        "autonomy_policy IN ('approval_required', 'autonomous')",
    )
    op.add_column(
        "workflows",
        sa.Column(
            "allow_dynamic_agent_proposals",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "workflows",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(op.f("ck_workflows_revision_positive"), "workflows", "revision >= 1")

    # --- tasks -------------------------------------------------------------------------------
    op.add_column(
        "workflow_tasks",
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "workflow_tasks", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "workflow_tasks",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_workflow_tasks_revision_positive"), "workflow_tasks", "revision >= 1"
    )
    op.create_check_constraint(
        op.f("ck_workflow_tasks_removed_is_cancelled"),
        "workflow_tasks",
        "removed_at IS NULL OR status = 'CANCELLED'",
    )

    # --- events ------------------------------------------------------------------------------
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.alter_column(
        "workflow_events", "event_type", type_=sa.String(length=23), existing_nullable=False
    )
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", NEW_EVENT_TYPES)
    )


def downgrade() -> None:
    # Events of the new types cannot be represented in the old schema. workflow_events is
    # append-only (UPDATE is rejected), so such rows are deleted rather than rewritten.
    op.execute(
        "DELETE FROM workflow_events WHERE event_type IN ("
        "'WORKFLOW_STATUS_CHANGED', 'TASK_STATUS_CHANGED', 'TASK_UPDATED', "
        "'TASK_REMOVED', 'DEPENDENCY_ADDED', 'DEPENDENCY_REMOVED')"
    )
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.alter_column(
        "workflow_events", "event_type", type_=sa.String(length=20), existing_nullable=False
    )
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", OLD_EVENT_TYPES)
    )

    op.drop_constraint(
        op.f("ck_workflow_tasks_removed_is_cancelled"), "workflow_tasks", type_="check"
    )
    op.drop_constraint(op.f("ck_workflow_tasks_revision_positive"), "workflow_tasks", type_="check")
    op.drop_column("workflow_tasks", "revision")
    op.drop_column("workflow_tasks", "removed_at")
    op.drop_column("workflow_tasks", "required")

    op.drop_constraint(op.f("ck_workflows_revision_positive"), "workflows", type_="check")
    op.drop_column("workflows", "revision")
    op.drop_column("workflows", "allow_dynamic_agent_proposals")
    op.drop_constraint(op.f("ck_workflows_autonomy_policy"), "workflows", type_="check")
    op.alter_column(
        "workflows", "autonomy_policy", type_=sa.String(length=50), existing_nullable=False
    )
    op.drop_constraint(op.f("ck_workflows_status"), "workflows", type_="check")
    # Map the new statuses onto the nearest old ones.
    op.execute(
        """
        UPDATE workflows SET status = CASE status
            WHEN 'AWAITING_APPROVAL' THEN 'WAITING_APPROVAL'
            WHEN 'PLANNING' THEN 'PENDING'
            WHEN 'READY' THEN 'PENDING'
            WHEN 'PAUSED' THEN 'RUNNING'
            WHEN 'BLOCKED' THEN 'RUNNING'
            ELSE status
        END
        """
    )
    op.alter_column("workflows", "status", type_=sa.String(length=16), existing_nullable=False)
    op.create_check_constraint(
        op.f("ck_workflows_status"), "workflows", _in("status", OLD_WORKFLOW_STATUSES)
    )
