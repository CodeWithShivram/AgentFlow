"""change projects

Adds change projects (Step 9.12, ADR-0059): a software project may name the completed base
project it changes (`base_workflow_id`, same workspace) and the verified commit its workspace
starts from (`base_head_sha`).

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-28 07:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("base_workflow_id", sa.Uuid(), nullable=True))
    op.add_column("workflows", sa.Column("base_head_sha", sa.String(length=40), nullable=True))
    op.create_index("ix_workflows_base_workflow_id", "workflows", ["base_workflow_id"])
    op.create_foreign_key(
        "fk_workflows_base_workflow_same_workspace",
        "workflows",
        "workflows",
        ["workspace_id", "base_workflow_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_workflows_base_with_commit"),
        "workflows",
        "(base_workflow_id IS NULL) = (base_head_sha IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_workflows_base_for_projects"),
        "workflows",
        "base_workflow_id IS NULL OR kind = 'SOFTWARE_PROJECT'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_workflows_base_for_projects"), "workflows", type_="check")
    op.drop_constraint(op.f("ck_workflows_base_with_commit"), "workflows", type_="check")
    op.drop_constraint("fk_workflows_base_workflow_same_workspace", "workflows", type_="foreignkey")
    op.drop_index("ix_workflows_base_workflow_id", table_name="workflows")
    op.drop_column("workflows", "base_head_sha")
    op.drop_column("workflows", "base_workflow_id")
