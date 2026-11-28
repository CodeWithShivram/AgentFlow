"""soft deleted workflows

Adds soft delete to workflows (Step 9.11, ADR-0058): `deleted_at` and `deleted_by_user_id`,
allowed only for workflows without work in flight, and the WORKFLOW_DELETED event type.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-28 04:52:37.146292+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The event types allowed since migration 0024.
EVENT_TYPES_0024 = (
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
    "INTERVENTION_OPENED",
    "INTERVENTION_RESOLVED",
)
NEW_EVENT_TYPE = "WORKFLOW_DELETED"
IDLE = "deleted_at IS NULL OR status IN ('PENDING', 'COMPLETED', 'FAILED', 'CANCELLED')"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _event_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", values)
    )


def upgrade() -> None:
    op.add_column("workflows", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflows", sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_workflows_deleted_by_user_id_users"),
        "workflows",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(op.f("ck_workflows_deleted_only_when_idle"), "workflows", IDLE)
    _event_type_check((*EVENT_TYPES_0024, NEW_EVENT_TYPE))


def downgrade() -> None:
    # workflow_events is append-only, so events of the new type are deleted, not rewritten.
    op.execute("DELETE FROM workflow_events WHERE event_type = 'WORKFLOW_DELETED'")
    _event_type_check(EVENT_TYPES_0024)
    op.drop_constraint(op.f("ck_workflows_deleted_only_when_idle"), "workflows", type_="check")
    op.drop_constraint(
        op.f("fk_workflows_deleted_by_user_id_users"), "workflows", type_="foreignkey"
    )
    op.drop_column("workflows", "deleted_by_user_id")
    op.drop_column("workflows", "deleted_at")
