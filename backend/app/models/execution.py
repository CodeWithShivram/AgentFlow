"""Background execution: transactional outbox, consumer inbox and agent execution attempts."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk


class OutboxStatus(StrEnum):
    PENDING = "PENDING"  # waiting to be published (or to be retried after a failure)
    PROCESSING = "PROCESSING"  # claimed by a relay under a lease
    PUBLISHED = "PUBLISHED"  # the broker accepted the message
    FAILED = "FAILED"  # gave up after the maximum attempts; kept for Phase 4 tooling


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class OutboxEvent(Base):
    """A message to publish, written in the same transaction as the change that requires it.

    `id` is the stable event ID: it is the broker message ID and the consumers' idempotency key.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "status <> 'PROCESSING' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="processing_has_lease",
        ),
        CheckConstraint(
            "(status = 'PUBLISHED') = (published_at IS NOT NULL)", name="published_has_timestamp"
        ),
        # Relay scans: due pending rows, and processing rows whose lease may have expired.
        Index(
            "ix_outbox_events_pending_available_at",
            "available_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_outbox_events_processing_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
        Index(
            "ix_outbox_events_published_published_at",
            "published_at",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(100))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    # Identifiers only: never prompts, credentials or large application data.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus, "status"), default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class InboxMessage(Base):
    """Consumed event IDs. Inserting first makes a consumer idempotent under redelivery."""

    __tablename__ = "inbox_messages"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )


class AgentExecution(Base):
    """One attempt by one agent version on one task. Never overwritten by later attempts."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_number"),
        UniqueConstraint("dispatch_event_id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_agent_executions_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_agent_executions_task",
        ),
        ForeignKeyConstraint(
            ["agent_id", "agent_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_agent_executions_agent_version",
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "(status = 'RUNNING') = (completed_at IS NULL)", name="finished_has_completed_at"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID]
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID] = mapped_column(index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(index=True)
    agent_version_id: Mapped[uuid.UUID]
    attempt_number: Mapped[int]
    status: Mapped[ExecutionStatus] = mapped_column(
        str_enum(ExecutionStatus, "status"), default=ExecutionStatus.RUNNING
    )
    # The outbox event that started this attempt (trace/correlation identifier).
    dispatch_event_id: Mapped[uuid.UUID]
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    # Safe, short diagnostics only; never stack traces or secrets.
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    result_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
