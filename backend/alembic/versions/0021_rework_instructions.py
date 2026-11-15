"""rework instructions

Adds `agent_executions.rework_instructions`: the corrective instructions an attempt was given from
the previous attempt's failure (Phase 5, Step 5.3, ADR-0032). Existing attempts get none.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-15 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_executions",
        sa.Column(
            "rework_instructions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_executions", "rework_instructions")
