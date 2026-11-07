"""auth sessions and invitations

Login sessions (refresh-token families), hashed single-use refresh tokens and workspace
invitations. See ADR-0007.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 17:05:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_DEFAULT = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def _timestamp(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=NOW if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("expires_at"),
        _timestamp("revoked_at", nullable=True),
        sa.Column("revoked_reason", sa.String(length=19), nullable=True),
        sa.CheckConstraint(
            "revoked_reason IN ('LOGOUT', 'REFRESH_TOKEN_REUSE')",
            name=op.f("ck_auth_sessions_revoked_reason"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name=op.f("ck_auth_sessions_revocation_has_reason"),
        ),
        sa.CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name=op.f("ck_auth_sessions_csrf_token_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"])
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("expires_at"),
        _timestamp("used_at", nullable=True),
        sa.CheckConstraint(
            "char_length(token_hash) = 64", name=op.f("ck_refresh_tokens_token_hash_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"])

    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=6), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("expires_at"),
        _timestamp("accepted_at", nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER')",
            name=op.f("ck_workspace_invitations_role"),
        ),
        sa.CheckConstraint("role <> 'OWNER'", name=op.f("ck_workspace_invitations_role_not_owner")),
        sa.CheckConstraint(
            "char_length(token_hash) = 64",
            name=op.f("ck_workspace_invitations_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)",
            name=op.f("ck_workspace_invitations_acceptance_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_invitations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_invited_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_accepted_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_workspace_invitations_token_hash")),
    )
    op.create_index(op.f("ix_workspace_invitations_email"), "workspace_invitations", ["email"])
    op.create_index(
        op.f("ix_workspace_invitations_workspace_id"), "workspace_invitations", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("workspace_invitations")
    op.drop_table("refresh_tokens")
    op.drop_table("auth_sessions")
