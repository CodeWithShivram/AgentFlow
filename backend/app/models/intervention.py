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
    text,
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
        # Every approval names the least role that may decide it (ADR-0038).
        CheckConstraint(
            "(type = 'APPROVAL') = (required_role IS NOT NULL)", name="approval_has_required_role"
        ),
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
    # APPROVAL only (ADR-0038): ADMIN, or OWNER for an agent granted a HIGH-risk tool. Set by the
    # platform when the request opens, never from the agent's own claims.
    required_role: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ApprovalDecision(Base):
    """The immutable record of one decision on an APPROVAL request (ADR-0038): who decided, in
    which role, what exactly (a fingerprint of the action and context shown), for which attempt.
    Written in the decision's transaction; never updated (a trigger refuses it)."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_approval_decisions_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_approval_decisions_task",
        ),
        CheckConstraint("decision IN ('approve', 'reject')", name="decision_known"),
        # A person decided, or the request expired (no person, no role).
        CheckConstraint("expired = (decided_by_user_id IS NULL)", name="person_or_expiry"),
        CheckConstraint(
            "(decided_by_user_id IS NULL) = (decided_role IS NULL)", name="role_with_person"
        ),
        Index("ix_approval_decisions_workspace_decided", "workspace_id", "decided_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    # A request is decided once.
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("human_interventions.id", ondelete="CASCADE"), unique=True
    )
    task_id: Mapped[uuid.UUID | None]
    # The attempt that asked (agent requests): an approval is bound to it and reaches only the
    # attempt that follows it.
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))
    then: Mapped[str | None] = mapped_column(String(8))
    # A person's note (people only; plain text, never sent to agents or put in events).
    note: Mapped[str | None] = mapped_column(Text)
    required_role: Mapped[str] = mapped_column(String(16))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_role: Mapped[str | None] = mapped_column(String(16))
    expired: Mapped[bool] = mapped_column(server_default=text("false"), default=False)
    # SHA-256 of the request's subject, action and context as shown (engine.action_fingerprint).
    action_fingerprint: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = created_at_column()
