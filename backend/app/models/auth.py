"""Login sessions (refresh-token families), refresh tokens and workspace invitations."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk
from app.models.tenancy import WorkspaceRole


class SessionRevocationReason(StrEnum):
    LOGOUT = "LOGOUT"
    REFRESH_TOKEN_REUSE = "REFRESH_TOKEN_REUSE"  # noqa: S105 - enum label, not a secret


class AuthSession(Base):
    """One login. Every refresh token issued for it belongs to this family.

    Access tokens name their session; revoking it invalidates them immediately.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("char_length(csrf_token_hash) = 64", name="csrf_token_hash_sha256"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revocation_has_reason"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 of the session's CSRF token; the raw token lives only in the browser cookie.
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    # Absolute lifetime: refreshing never extends a session past this point.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[SessionRevocationReason | None] = mapped_column(
        str_enum(SessionRevocationReason, "revoked_reason")
    )


class RefreshToken(Base):
    """A single-use refresh token. Only its SHA-256 hash is stored."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (CheckConstraint("char_length(token_hash) = 64", name="token_hash_sha256"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set when exchanged for a new token. Presenting a used token again means it leaked.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint("role <> 'OWNER'", name="role_not_owner"),
        CheckConstraint("char_length(token_hash) = 64", name="token_hash_sha256"),
        CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)", name="acceptance_complete"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Normalized (lower-case) email of the invitee; only that account may accept.
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[WorkspaceRole] = mapped_column(str_enum(WorkspaceRole, "role"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
