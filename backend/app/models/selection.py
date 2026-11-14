"""Agent selection rationales (ADR-0023): one append-only row per assignment.

Records why the assigned agent was chosen, as computed by the server's selection rules at that
moment: its eligibility, score, rank among the candidates, the top suggestion and the factor by
factor rationale. A trigger rejects UPDATE; rows are history.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk


class AgentSelection(Base):
    __tablename__ = "agent_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_agent_selections_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_agent_selections_task",
        ),
        ForeignKeyConstraint(
            ["agent_id", "agent_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_agent_selections_agent_version",
        ),
        CheckConstraint("(rank IS NULL) = (NOT eligible)", name="rank_iff_eligible"),
        CheckConstraint("rank IS NULL OR rank >= 1", name="rank_positive"),
        CheckConstraint("candidate_count >= 1", name="candidate_count_positive"),
        CheckConstraint("score >= 0", name="score_non_negative"),
        Index("ix_agent_selections_task_id_created_at", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID]
    agent_version_id: Mapped[uuid.UUID]
    # The member who assigned; NULL is reserved for platform decisions (none yet).
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    rules_version: Mapped[str] = mapped_column(String(32))
    eligible: Mapped[bool]
    score: Mapped[Decimal] = mapped_column(Numeric(6, 1))
    rank: Mapped[int | None]
    # The best eligible candidate when the decision was made (NULL if none was eligible).
    top_agent_id: Mapped[uuid.UUID | None]
    candidate_count: Mapped[int]
    # The chosen candidate's factors: [{name, outcome, points, detail}].
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
