"""replan drafts

Adds `workflow_plans.kind` ("plan" or "replan") and `workflow_plans.basis`, the completed tasks a
replan draft builds on (Phase 5, Step 5.4, ADR-0033). Existing drafts are plans.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-15 20:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_plans",
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'plan'"), nullable=False),
    )
    op.add_column(
        "workflow_plans",
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_workflow_plans_kind_known"), "workflow_plans", "kind IN ('plan', 'replan')"
    )
    op.create_check_constraint(
        op.f("ck_workflow_plans_replan_has_basis"),
        "workflow_plans",
        "(kind = 'replan') = (basis IS NOT NULL)",
    )


def downgrade() -> None:
    # Replan drafts cannot be expressed without their basis; they are dropped with it.
    op.execute("DELETE FROM workflow_plans WHERE kind = 'replan'")
    op.drop_constraint(op.f("ck_workflow_plans_replan_has_basis"), "workflow_plans", type_="check")
    op.drop_constraint(op.f("ck_workflow_plans_kind_known"), "workflow_plans", type_="check")
    op.drop_column("workflow_plans", "basis")
    op.drop_column("workflow_plans", "kind")
