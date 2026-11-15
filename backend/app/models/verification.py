"""Verification results (handoff section 5.6, ADR-0030): one append-only row per verifier per
attempt.

Verification is separate from agent execution: it records which checks ran on one attempt's
submission, the issues found (codes, severities and the criterion, deliverable or schema
location they concern; never agent text) and the outcome. A trigger rejects UPDATE; rows are
history.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class VerificationOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class VerificationResult(Base):
    __tablename__ = "verification_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_verification_results_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_verification_results_task",
        ),
        # One result per verifier per attempt; semantic evaluation (Step 5.2) adds its own.
        UniqueConstraint("execution_id", "verifier"),
        Index("ix_verification_results_task_id_created_at", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID]
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE")
    )
    # The verifier and its rules version, e.g. "deterministic/v1".
    verifier: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[VerificationOutcome] = mapped_column(str_enum(VerificationOutcome, "outcome"))
    # Checks performed: [{name, outcome: passed|failed|skipped}].
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Issues found: [{code, severity: error|warning, criterion_id?, deliverable?, location?}].
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
