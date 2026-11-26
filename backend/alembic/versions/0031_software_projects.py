"""software projects

Adds the software project workflow kind (Phase 9, Step 9.2, ADR-0048): `workflows.kind`
(GENERAL by default, so existing workflows are unchanged) and `workflows.project_spec`, the
confirmed plan's project (name, stack profile, architecture, assumptions), set only for
software projects; and per task `workflow_tasks.file_scope`, the files it alone may write, and
`effort_points` (1-13), copied from a version 2 plan when it is confirmed.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-26 12:05:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_tasks",
        sa.Column(
            "file_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("workflow_tasks", sa.Column("effort_points", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_workflow_tasks_effort_points_range"),
        "workflow_tasks",
        "effort_points IS NULL OR effort_points BETWEEN 1 AND 13",
    )
    op.add_column(
        "workflows",
        sa.Column(
            "kind",
            sa.Enum(
                "GENERAL",
                "SOFTWARE_PROJECT",
                name="kind",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'GENERAL'"),
            nullable=False,
        ),
    )
    op.add_column(
        "workflows",
        sa.Column(
            "project_spec",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_workflows_project_spec_for_projects"),
        "workflows",
        "project_spec IS NULL OR kind = 'SOFTWARE_PROJECT'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_workflows_project_spec_for_projects"), "workflows", type_="check")
    op.drop_column("workflows", "project_spec")
    op.drop_column("workflows", "kind")
    op.drop_constraint(
        op.f("ck_workflow_tasks_effort_points_range"), "workflow_tasks", type_="check"
    )
    op.drop_column("workflow_tasks", "effort_points")
    op.drop_column("workflow_tasks", "file_scope")
