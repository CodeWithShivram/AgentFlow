"""Agent definitions, immutable versions, dynamic proposals and the registry audit trail.

Lifecycle: an agent has an editable draft configuration; publishing snapshots it into a new
immutable agent_versions row and makes that the active version. Execution always references a
specific version, so later edits never change history (ADR-0008).
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


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AgentProposalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AgentAuditAction(StrEnum):
    CREATED = "CREATED"
    DRAFT_UPDATED = "DRAFT_UPDATED"
    VERSION_PUBLISHED = "VERSION_PUBLISHED"
    ACTIVATED = "ACTIVATED"
    DEACTIVATED = "DEACTIVATED"
    PROPOSAL_SUBMITTED = "PROPOSAL_SUBMITTED"
    PROPOSAL_APPROVED = "PROPOSAL_APPROVED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        # Built-in agents have no workspace; NULLS NOT DISTINCT keeps their names unique too.
        UniqueConstraint("workspace_id", "name", postgresql_nulls_not_distinct=True),
        # The active version must be a version of this agent. use_alter breaks the
        # agents <-> agent_versions creation cycle. NO ACTION (checked at statement end)
        # lets an agent and its versions be deleted together.
        ForeignKeyConstraint(
            ["id", "active_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            use_alter=True,
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "status = 'INACTIVE' OR active_version_id IS NOT NULL",
            name="active_requires_version",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # NULL = built-in agent available to every workspace (read-only to workspaces).
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[AgentStatus] = mapped_column(
        str_enum(AgentStatus, "status"), default=AgentStatus.INACTIVE, index=True
    )
    active_version_id: Mapped[uuid.UUID | None]
    # Editable working copy; validated against AgentConfiguration. Published by snapshotting.
    draft_configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Optimistic concurrency: every mutation must name the revision it read, then increments it.
    revision: Mapped[int] = mapped_column(server_default=text("1"), default=1)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class AgentVersion(Base):
    """Immutable snapshot of an agent's configuration. A trigger rejects UPDATE."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version"),
        # Target of agents' composite active-version foreign key.
        UniqueConstraint("agent_id", "id"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[int]
    # Skills, model and tool permissions. Never holds provider secrets (handoff 5.4).
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    # NULL for system-seeded built-in versions.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class AgentProposal(Base):
    """A proposed new specialist. Inert until an authorized reviewer approves it.

    A trigger freezes the proposed fields once submitted; only the decision may change.
    """

    __tablename__ = "agent_proposals"
    __table_args__ = (
        CheckConstraint(
            "(status = 'PENDING') = (reviewed_at IS NULL)", name="decision_has_timestamp"
        ),
        CheckConstraint("status <> 'APPROVED' OR agent_id IS NOT NULL", name="approval_has_agent"),
        CheckConstraint(
            "reviewed_by IS NULL OR requested_by IS NULL OR reviewed_by <> requested_by",
            name="reviewer_not_requester",
        ),
        Index("ix_agent_proposals_workspace_id_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    rationale: Mapped[str] = mapped_column(Text)
    proposed_configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[AgentProposalStatus] = mapped_column(
        str_enum(AgentProposalStatus, "status"), default=AgentProposalStatus.PENDING
    )
    # NULL when proposed by the planner (Phase 3) rather than a person.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    # The agent created on approval.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class AgentAuditEvent(Base):
    """Append-only registry history. A trigger rejects UPDATE."""

    __tablename__ = "agent_audit_events"
    __table_args__ = (
        Index("ix_agent_audit_events_agent_id_created_at", "agent_id", "created_at"),
        Index("ix_agent_audit_events_workspace_id_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # NULL for built-in agents managed by the platform.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_proposals.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[AgentAuditAction] = mapped_column(str_enum(AgentAuditAction, "action"))
    # NULL for platform actions (built-in seeding).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Small structured context (e.g. version number). Never configuration secrets.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()
