"""workflow pause reasons

Records why a PAUSED workflow is paused, by whom and when, with a person's optional note
(Phase 6, Step 6.2, ADR-0036). Workflows already paused are backfilled from their latest pause
event: DEADLINE_PASSED when the recovery sweep paused them, otherwise USER.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-16 10:04:33.747172+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "pause_reason",
            sa.Enum(
                "USER",
                "DEADLINE_PASSED",
                "AWAITING_DECISION",
                name="pause_reason",
                native_enum=False,
                create_constraint=True,
                length=17,
            ),
            nullable=True,
        ),
    )
    op.add_column("workflows", sa.Column("paused_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("workflows", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflows", sa.Column("pause_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_workflows_paused_by_user_id_users"),
        "workflows",
        "users",
        ["paused_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE workflows AS w
        SET pause_reason = CASE
                WHEN latest.reason = 'DEADLINE_PASSED' THEN 'DEADLINE_PASSED' ELSE 'USER'
            END,
            paused_at = COALESCE(latest.created_at, w.updated_at)
        FROM workflows AS paused
        LEFT JOIN LATERAL (
            SELECT e.payload ->> 'reason' AS reason, e.created_at
            FROM workflow_events AS e
            WHERE e.workflow_id = paused.id AND e.payload ->> 'to' = 'PAUSED'
            ORDER BY e.sequence DESC
            LIMIT 1
        ) AS latest ON true
        WHERE w.id = paused.id AND w.status = 'PAUSED'
        """
    )
    op.create_check_constraint(
        op.f("ck_workflows_paused_iff_reason"),
        "workflows",
        "(status = 'PAUSED') = (pause_reason IS NOT NULL AND paused_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_workflows_paused_iff_reason"), "workflows", type_="check")
    op.drop_constraint(
        op.f("fk_workflows_paused_by_user_id_users"), "workflows", type_="foreignkey"
    )
    op.drop_column("workflows", "pause_note")
    op.drop_column("workflows", "paused_at")
    op.drop_column("workflows", "paused_by_user_id")
    op.drop_column("workflows", "pause_reason")
