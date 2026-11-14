"""running execution index

Adds a partial index on running attempts for the recovery sweep that finds attempts stranded by
a lost worker (Phase 4, Step 4.5, ADR-0029).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-14 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_executions_running_started_at",
        "agent_executions",
        ["started_at"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_executions_running_started_at",
        table_name="agent_executions",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
