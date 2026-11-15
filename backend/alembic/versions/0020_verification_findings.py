"""verification findings

Adds `verification_results.findings`, the semantic evaluator's structured findings per attempt
(Phase 5, Step 5.2, ADR-0031). NULL for deterministic checks.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-15 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "verification_results",
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("verification_results", "findings")
