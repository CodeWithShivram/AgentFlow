"""Workspace applications (ADR-0049): one row per accepted attempt applied (or not) to its
software project's workspace. Append-only history, written only by the apply job."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class WorkspaceApplicationStatus(StrEnum):
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class WorkspaceApplication(Base):
    __tablename__ = "workspace_applications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_workspace_applications_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_workspace_applications_task",
        ),
        CheckConstraint(
            "(status = 'APPLIED') = (error_code IS NULL AND head_sha IS NOT NULL)",
            name="applied_or_error",
        ),
        Index("ix_workspace_applications_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID]
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[WorkspaceApplicationStatus] = mapped_column(
        str_enum(WorkspaceApplicationStatus, "status")
    )
    # A fixed platform code (never Git output or a path) when FAILED.
    error_code: Mapped[str | None] = mapped_column(String(64))
    # The commit this application made; NULL when the scope already matched.
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    files_written: Mapped[int] = mapped_column(default=0, server_default="0")
    files_removed: Mapped[int] = mapped_column(default=0, server_default="0")
    tree_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
