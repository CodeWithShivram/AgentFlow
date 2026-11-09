"""background execution

Transactional outbox, consumer inbox, agent execution attempts and expiry indexes used by
scheduled cleanup. See ADR-0010.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09 15:33:03.372476+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbox_messages",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id", "consumer", name=op.f("pk_inbox_messages")),
    )
    op.create_index(
        op.f("ix_inbox_messages_processed_at"), "inbox_messages", ["processed_at"], unique=False
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')",
            name=op.f("ck_outbox_events_status"),
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'PUBLISHED') = (published_at IS NOT NULL)",
            name=op.f("ck_outbox_events_published_has_timestamp"),
        ),
        sa.CheckConstraint(
            "status <> 'PROCESSING' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_outbox_events_processing_has_lease"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_events_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_outbox_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_pending_available_at",
        "outbox_events",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_outbox_events_processing_lease_expires_at",
        "outbox_events",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    op.create_index(
        "ix_outbox_events_published_published_at",
        "outbox_events",
        ["published_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_index(
        op.f("ix_outbox_events_workspace_id"), "outbox_events", ["workspace_id"], unique=False
    )
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED')",
            name=op.f("ck_agent_executions_status"),
        ),
        sa.Column("dispatch_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING') = (completed_at IS NULL)",
            name=op.f("ck_agent_executions_finished_has_completed_at"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name=op.f("ck_agent_executions_attempt_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "agent_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            name="fk_agent_executions_agent_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_agent_executions_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_agent_executions_workflow",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_executions")),
        sa.UniqueConstraint(
            "dispatch_event_id", name=op.f("uq_agent_executions_dispatch_event_id")
        ),
        sa.UniqueConstraint(
            "task_id", "attempt_number", name=op.f("uq_agent_executions_task_id_attempt_number")
        ),
    )
    op.create_index(
        op.f("ix_agent_executions_agent_id"), "agent_executions", ["agent_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_executions_task_id"), "agent_executions", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_refresh_tokens_expires_at"), "refresh_tokens", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_workspace_invitations_expires_at"),
        "workspace_invitations",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workspace_invitations_expires_at"), table_name="workspace_invitations")
    op.drop_index(op.f("ix_refresh_tokens_expires_at"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_agent_executions_task_id"), table_name="agent_executions")
    op.drop_index(op.f("ix_agent_executions_agent_id"), table_name="agent_executions")
    op.drop_table("agent_executions")
    op.drop_index(op.f("ix_outbox_events_workspace_id"), table_name="outbox_events")
    op.drop_index(
        "ix_outbox_events_published_published_at",
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.drop_index(
        "ix_outbox_events_processing_lease_expires_at",
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    op.drop_index(
        "ix_outbox_events_pending_available_at",
        table_name="outbox_events",
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_table("outbox_events")
    op.drop_index(op.f("ix_inbox_messages_processed_at"), table_name="inbox_messages")
    op.drop_table("inbox_messages")
