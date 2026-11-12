"""prompt analysis

Adds the prompt-analysis state to workflows and three workflow event types (Phase 2,
Step 2.2). The validated analysis itself goes into the existing `normalized_requirements`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 16:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES_0004 = (
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
)
ANALYSIS_EVENT_TYPES = ("ANALYSIS_REQUESTED", "REQUIREMENTS_ANALYZED", "ANALYSIS_FAILED")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _event_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflow_events_event_type"), "workflow_events", type_="check")
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type"), "workflow_events", _in("event_type", values)
    )


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "analysis_status",
            sa.Enum(
                "NOT_REQUESTED",
                "PENDING",
                "READY",
                "FAILED",
                name="analysis_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'NOT_REQUESTED'"),
            nullable=False,
        ),
    )
    op.add_column("workflows", sa.Column("analysis_request_id", sa.Uuid(), nullable=True))
    # The longest new type (21 characters) fits the existing VARCHAR(23).
    _event_type_check((*EVENT_TYPES_0004, *ANALYSIS_EVENT_TYPES))


def downgrade() -> None:
    # workflow_events is append-only (UPDATE is rejected), so events of the new types are
    # deleted rather than rewritten before the narrower CHECK returns.
    op.execute(
        "DELETE FROM workflow_events "
        "WHERE event_type IN ('ANALYSIS_REQUESTED', 'REQUIREMENTS_ANALYZED', 'ANALYSIS_FAILED')"
    )
    _event_type_check(EVENT_TYPES_0004)
    op.drop_column("workflows", "analysis_request_id")
    op.drop_column("workflows", "analysis_status")
