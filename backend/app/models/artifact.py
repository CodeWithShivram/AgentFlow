"""Stored artifacts (handoff section 10 `task_artifacts`, ADR-0047): metadata and checksum in
PostgreSQL, bytes in the configured artifact storage (`app.artifacts.storage`), never in the
database.

Only platform code creates artifacts. The storage key is derived from ids
(`<workspace_id>/<artifact_id>`), never from a name a person, agent or model chose. Deleting
an artifact marks the row DELETED (kept as history) and removes the stored bytes.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class ArtifactKind(StrEnum):
    # A packaged project (Step 9.6).
    PROJECT_ARCHIVE = "PROJECT_ARCHIVE"
    # A human-readable build or verification report.
    BUILD_REPORT = "BUILD_REPORT"
    # Any other file produced for one task attempt.
    TASK_OUTPUT = "TASK_OUTPUT"


class ArtifactStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DELETED = "DELETED"


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_artifacts_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_artifacts_task",
        ),
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("(status = 'DELETED') = (deleted_at IS NOT NULL)", name="deleted_has_time"),
        CheckConstraint("execution_id IS NULL OR task_id IS NOT NULL", name="execution_has_task"),
        Index("ix_artifacts_workflow_created", "workspace_id", "workflow_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID | None]
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE")
    )
    kind: Mapped[ArtifactKind] = mapped_column(str_enum(ArtifactKind, "kind"))
    # A safe display file name (`app.artifacts.storage.safe_filename`), used for downloads only.
    filename: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(100))
    # `<workspace_id>/<artifact_id>`; never exposed to clients.
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[ArtifactStatus] = mapped_column(
        str_enum(ArtifactStatus, "status"),
        default=ArtifactStatus.AVAILABLE,
        server_default=text("'AVAILABLE'"),
    )
    created_at: Mapped[datetime] = created_at_column()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
