"""platform agent proposals

Links platform-filed agent proposals to the workflow task that had no suitable agent (Phase 3,
Step 3.7, ADR-0024), and freezes those links like the rest of the proposed content.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-14 08:20:40.049053+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FROZEN_0003 = """               OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN"""
_FROZEN_0014 = """               OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
               OR NEW.source_workflow_id IS DISTINCT FROM OLD.source_workflow_id
               OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id THEN"""


def _freeze_function(frozen_tail: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION agent_proposals_freeze_content() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.name IS DISTINCT FROM OLD.name
               OR NEW.category IS DISTINCT FROM OLD.category
               OR NEW.description IS DISTINCT FROM OLD.description
               OR NEW.rationale IS DISTINCT FROM OLD.rationale
               OR NEW.proposed_configuration IS DISTINCT FROM OLD.proposed_configuration
{frozen_tail}
                RAISE EXCEPTION 'proposed agent content cannot change after submission'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status <> 'PENDING' THEN
                RAISE EXCEPTION 'proposal has already been decided'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """


def upgrade() -> None:
    op.add_column("agent_proposals", sa.Column("source_workflow_id", sa.Uuid(), nullable=True))
    op.add_column("agent_proposals", sa.Column("source_task_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_agent_proposals_task_needs_workflow"),
        "agent_proposals",
        "source_task_id IS NULL OR source_workflow_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_agent_proposals_source_workflow",
        "agent_proposals",
        "workflows",
        ["workspace_id", "source_workflow_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_agent_proposals_source_task",
        "agent_proposals",
        "workflow_tasks",
        ["source_workflow_id", "source_task_id"],
        ["workflow_id", "id"],
    )
    op.execute(_freeze_function(_FROZEN_0014))


def downgrade() -> None:
    op.execute(_freeze_function(_FROZEN_0003))
    op.drop_constraint("fk_agent_proposals_source_task", "agent_proposals", type_="foreignkey")
    op.drop_constraint("fk_agent_proposals_source_workflow", "agent_proposals", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_agent_proposals_task_needs_workflow"), "agent_proposals", type_="check"
    )
    op.drop_column("agent_proposals", "source_task_id")
    op.drop_column("agent_proposals", "source_workflow_id")
