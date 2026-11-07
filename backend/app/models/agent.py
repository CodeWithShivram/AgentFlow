"""Agent definitions and their immutable versions."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk


class AgentStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


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
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # NULL = built-in agent available to every workspace.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[AgentStatus] = mapped_column(
        str_enum(AgentStatus, "status"), default=AgentStatus.DISABLED, index=True
    )
    active_version_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class AgentVersion(Base):
    """Immutable snapshot of an agent's configuration. Changes create a new version."""

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
