"""Generated plan drafts (Phase 2, Step 2.3, ADR-0015).

Each successful generation is an immutable draft version: a trigger rejects UPDATE. Drafts are
proposals only; workflow tasks are created from one when the user confirms it (Step 2.5).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint, text
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
        CheckConstraint("kind IN ('plan', 'replan')", name="kind_known"),
        CheckConstraint("(kind = 'replan') = (basis IS NOT NULL)", name="replan_has_basis"),
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
    # "replan" drafts revise the remaining work of a paused or blocked workflow (ADR-0033).
    kind: Mapped[str] = mapped_column(String(16), server_default=text("'plan'"), default="plan")
    # A replan's completed tasks when it was requested: [{key, task_id, title}]. The draft's
    # `depends_on_completed` keys refer to these.
    basis: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = created_at_column()
