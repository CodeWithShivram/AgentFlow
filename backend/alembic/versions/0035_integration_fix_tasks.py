"""integration fix tasks

Adds integration rework (Step 9.9, ADR-0056): `workflow_tasks.fixes_task_id` names the task whose
files a platform fix task fixes (same workflow, composite FK) and `fixes_integration_id` the
failed integration run that asked for it; both NULL for planned tasks, both set for fix tasks.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-27 05:12:03.144339+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_tasks", sa.Column("fixes_task_id", sa.Uuid(), nullable=True))
    op.add_column("workflow_tasks", sa.Column("fixes_integration_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_workflow_tasks_fixes_integration_id_project_integrations"),
        "workflow_tasks",
        "project_integrations",
        ["fixes_integration_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workflow_tasks_fixes_task",
        "workflow_tasks",
        "workflow_tasks",
        ["workflow_id", "fixes_task_id"],
        ["workflow_id", "id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_workflow_tasks_fix_has_integration"),
        "workflow_tasks",
        "(fixes_task_id IS NULL) = (fixes_integration_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_workflow_tasks_fix_has_integration"), "workflow_tasks", type_="check"
    )
    op.drop_constraint("fk_workflow_tasks_fixes_task", "workflow_tasks", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_workflow_tasks_fixes_integration_id_project_integrations"),
        "workflow_tasks",
        type_="foreignkey",
    )
    op.drop_column("workflow_tasks", "fixes_integration_id")
    op.drop_column("workflow_tasks", "fixes_task_id")
