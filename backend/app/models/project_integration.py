"""Project integrations (ADR-0051): each run of a software project's integration gate, the
profile's checks executed in the sandbox on the workspace's composed tree at one commit.

A row starts RUNNING and is finished once as PASSED, FAILED or NOT_RUN (the sandbox could not
run; never treated as passed); a trigger refuses any other change. `checks` holds each check's
name, outcome, exit code, duration and capped, redacted output: untrusted text, shown as plain
text only and never logged.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class IntegrationStatus(StrEnum):
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class ProjectIntegration(Base):
    __tablename__ = "project_integrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_project_integrations_workflow",
        ),
        CheckConstraint("head_sha ~ '^[0-9a-f]{40}$'", name="head_sha_hex"),
        CheckConstraint(
            "(status = 'RUNNING') = (finished_at IS NULL)", name="finished_unless_running"
        ),
        Index("ix_project_integrations_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    # The workspace commit that was checked.
    head_sha: Mapped[str] = mapped_column(String(40))
    stack_profile: Mapped[str] = mapped_column(String(64))
    status: Mapped[IntegrationStatus] = mapped_column(
        str_enum(IntegrationStatus, "status"), default=IntegrationStatus.RUNNING
    )
    # [{name, outcome: passed|failed|not_run, exit_code, timed_out, duration_ms, output}]
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # A fixed code for FAILED or NOT_RUN (e.g. CHECKS_FAILED, DOCKER_UNAVAILABLE).
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
