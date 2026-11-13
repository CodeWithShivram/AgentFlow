"""agent contract

Adds expected deliverables to tasks (part of the task contract) and the validated submission
of each successful execution attempt (Phase 3, Step 3.1, ADR-0018).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_tasks",
        sa.Column(
            "deliverables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column("submission", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_agent_executions_submission_only_when_succeeded"),
        "agent_executions",
        "submission IS NULL OR status = 'SUCCEEDED'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_executions_submission_only_when_succeeded"),
        "agent_executions",
        type_="check",
    )
    op.drop_column("agent_executions", "submission")
    op.drop_column("workflow_tasks", "deliverables")
