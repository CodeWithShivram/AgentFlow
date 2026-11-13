"""tool invocations

Adds `tool_invocations`, one row per call made through the tool gateway (handoff §11,
ADR-0019), under row-level security like every workspace-owned table (ADR-0012).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-13 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUCCEEDED",
                "DENIED",
                "FAILED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_reference", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED') = (error_code IS NULL)",
            name=op.f("ck_tool_invocations_error_code_iff_unsuccessful"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_tool_invocations_duration_non_negative")
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_tool_invocations_input_hash_is_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_tool_invocations_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_tool_invocations_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_tool_invocations_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_tool_invocations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_invocations")),
    )
    op.create_index(op.f("ix_tool_invocations_execution_id"), "tool_invocations", ["execution_id"])
    op.create_index(op.f("ix_tool_invocations_task_id"), "tool_invocations", ["task_id"])
    op.create_index(
        "ix_tool_invocations_workspace_id_created_at",
        "tool_invocations",
        ["workspace_id", "created_at"],
    )

    op.execute("ALTER TABLE tool_invocations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_invocations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tool_invocations_tenant ON tool_invocations FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY tool_invocations_platform ON tool_invocations FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tool_invocations_platform ON tool_invocations")
    op.execute("DROP POLICY tool_invocations_tenant ON tool_invocations")
    op.drop_index("ix_tool_invocations_workspace_id_created_at", table_name="tool_invocations")
    op.drop_index(op.f("ix_tool_invocations_task_id"), table_name="tool_invocations")
    op.drop_index(op.f("ix_tool_invocations_execution_id"), table_name="tool_invocations")
    op.drop_table("tool_invocations")
