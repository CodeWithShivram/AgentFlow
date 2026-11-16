"""intervention expiry

Allows the EXPIRED status on `human_interventions`: an agent's approval request nobody decided
before its `expires_at` (Phase 6, Step 6.3, ADR-0037). The downgrade turns expired requests into
WITHDRAWN ones, keeping their resolution, so the old check holds.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-16 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _status_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_human_interventions_status"), "human_interventions", type_="check")
    op.create_check_constraint(
        op.f("ck_human_interventions_status"),
        "human_interventions",
        f"status IN ({', '.join(repr(v) for v in values)})",
    )


def upgrade() -> None:
    _status_check(("OPEN", "RESOLVED", "WITHDRAWN", "EXPIRED"))


def downgrade() -> None:
    op.execute("UPDATE human_interventions SET status = 'WITHDRAWN' WHERE status = 'EXPIRED'")
    _status_check(("OPEN", "RESOLVED", "WITHDRAWN"))
