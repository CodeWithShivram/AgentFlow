"""Human interventions (handoff section 5.7, ADR-0035): requests that need a person.

Types (handoff): CLARIFICATION, APPROVAL, REJECTION and MANUAL_RECOVERY. A `subject` says what a
request is about (an analysis question, a plan approval, an agent's action, an escalated task).
Requested actions and contexts may hold model or agent text (a question, an agent's proposed
action): untrusted, shown as plain text only. Decisions are attributable: `resolved_by_user_id`
names the person, NULL only when the platform closed a request because the work moved on.
"""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk


class InterventionType(StrEnum):
    CLARIFICATION = "CLARIFICATION"
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    MANUAL_RECOVERY = "MANUAL_RECOVERY"


class InterventionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"  # a person answered, approved or rejected, or acted on it
    WITHDRAWN = "WITHDRAWN"  # the platform closed it: superseded, or the work moved on
    EXPIRED = "EXPIRED"  # nobody decided in time; handled as a rejection (ADR-0037)


class InterventionSubject(StrEnum):
    ANALYSIS_QUESTION = "analysis_question"
    PLAN_APPROVAL = "plan_approval"
    AGENT_ACTION = "agent_action"
    TASK_ESCALATION = "task_escalation"


class HumanIntervention(Base):
    __tablename__ = "human_interventions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_human_interventions_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_human_interventions_task",
        ),
        CheckConstraint("(status = 'OPEN') = (resolved_at IS NULL)", name="open_iff_unresolved"),
        CheckConstraint("requested_by IN ('SYSTEM', 'AGENT', 'USER')", name="requested_by_known"),
        # One request per source (e.g. an analysis question or an attempt), so retries of the
        # code that opens it never duplicate it.
        UniqueConstraint("workflow_id", "source_key"),
        Index("ix_human_interventions_workspace_status", "workspace_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID | None]
    type: Mapped[InterventionType] = mapped_column(str_enum(InterventionType, "type"))
    subject: Mapped[str] = mapped_column(String(32))
    status: Mapped[InterventionStatus] = mapped_column(
        str_enum(InterventionStatus, "status"), default=InterventionStatus.OPEN
    )
    # What is asked or proposed. May be model or agent text: plain text only.
    requested_action: Mapped[str] = mapped_column(Text)
    # Why, risks, consequences, evidence references.
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str] = mapped_column(String(16))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    requested_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT")
    )
    source_key: Mapped[str] = mapped_column(String(160))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
