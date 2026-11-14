"""orchestration next step

Stores the orchestration graph's latest assessment of each workflow (Phase 4, Step 4.1,
ADR-0025). Workers already hold UPDATE on workflows; no new table or grant.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("next_step", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "next_step")
