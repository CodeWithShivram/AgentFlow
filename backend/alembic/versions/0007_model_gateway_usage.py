"""model gateway usage

Adds `llm_usage`, one row per model call written by the model gateway (ADR-0013, handoff §12),
and puts it under row-level security like every workspace-owned table (ADR-0012).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12 14:56:25.545694+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUCCEEDED",
                "FAILED",
                "INVALID_OUTPUT",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "other_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("output_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("total_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("pricing_version", sa.String(length=100), nullable=True),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED') = (error_code IS NULL)",
            name=op.f("ck_llm_usage_error_code_iff_unsuccessful"),
        ),
        sa.CheckConstraint(
            "(input_cost IS NULL AND output_cost IS NULL AND total_cost IS NULL "
            "AND currency IS NULL AND pricing_version IS NULL) "
            "OR (input_cost IS NOT NULL AND output_cost IS NOT NULL AND total_cost IS NOT NULL "
            "AND currency IS NOT NULL AND pricing_version IS NOT NULL)",
            name=op.f("ck_llm_usage_costs_all_or_nothing"),
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (cached_input_tokens IS NULL OR cached_input_tokens >= 0)",
            name=op.f("ck_llm_usage_tokens_non_negative"),
        ),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_llm_usage_latency_non_negative")),
        sa.CheckConstraint(
            "task_id IS NULL OR workflow_id IS NOT NULL",
            name=op.f("ck_llm_usage_task_needs_workflow"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_llm_usage_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_llm_usage_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_llm_usage_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_llm_usage_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage")),
    )
    op.create_index(op.f("ix_llm_usage_execution_id"), "llm_usage", ["execution_id"], unique=False)
    op.create_index(op.f("ix_llm_usage_task_id"), "llm_usage", ["task_id"], unique=False)
    op.create_index(op.f("ix_llm_usage_workflow_id"), "llm_usage", ["workflow_id"], unique=False)
    op.create_index(
        "ix_llm_usage_workspace_id_created_at",
        "llm_usage",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.execute("ALTER TABLE llm_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_usage FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY llm_usage_tenant ON llm_usage FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY llm_usage_platform ON llm_usage FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY llm_usage_platform ON llm_usage")
    op.execute("DROP POLICY llm_usage_tenant ON llm_usage")
    op.drop_index("ix_llm_usage_workspace_id_created_at", table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_workflow_id"), table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_task_id"), table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_execution_id"), table_name="llm_usage")
    op.drop_table("llm_usage")
