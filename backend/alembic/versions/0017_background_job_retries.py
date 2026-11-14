"""background job retries

Adds the BACKGROUND_JOB_RETRIED workflow event, recorded when an admin requeues a failed outbox
event (Phase 4, Step 4.3, ADR-0027).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-14 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES_0009 = (
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
)
EVENT_TYPES_0016 = (*EVENT_TYPES_0009, "PLAN_CONFIRMED")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _event_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", values)
    )


def upgrade() -> None:
    _event_type_check((*EVENT_TYPES_0016, "BACKGROUND_JOB_RETRIED"))


def downgrade() -> None:
    # workflow_events is append-only, so events of the new type are deleted, not rewritten.
    op.execute("DELETE FROM workflow_events WHERE event_type = 'BACKGROUND_JOB_RETRIED'")
    _event_type_check(EVENT_TYPES_0016)
