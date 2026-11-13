"""Generated plan drafts (Phase 2, Step 2.3, ADR-0015).

Each successful generation is an immutable draft version: a trigger rejects UPDATE. Drafts are
proposals only; workflow tasks are created from one when the user confirms it (Step 2.5).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk


class WorkflowPlan(Base):
    __tablename__ = "workflow_plans"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_workflow_plans_workflow",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("task_count BETWEEN 1 AND 25", name="task_count_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID]
    workflow_id: Mapped[uuid.UUID]
    version: Mapped[int]
    # The validated app.planning.plan.PlanProposal.
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB)
    task_count: Mapped[int]
    # The outbox event of the request that produced it, and the prompt version used.
    request_id: Mapped[uuid.UUID]
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
