"""Queries for login sessions and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession, RefreshToken, User


async def get_active_session(
    session: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> tuple[AuthSession, User] | None:
    statement = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    row = (await session.execute(statement)).one_or_none()
    return None if row is None else (row[0], row[1])


async def lock_refresh_token(
    session: AsyncSession, token_hash: str
) -> tuple[RefreshToken, AuthSession] | None:
    """Loads a refresh token and its session, row-locked so concurrent refreshes serialize."""
    statement = (
        select(RefreshToken, AuthSession)
        .join(AuthSession, AuthSession.id == RefreshToken.session_id)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(statement)).one_or_none()
    return None if row is None else (row[0], row[1])


async def lock_session(session: AsyncSession, session_id: uuid.UUID) -> AuthSession | None:
    statement = (
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()
