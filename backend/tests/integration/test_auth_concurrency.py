"""Concurrent signup, refresh and invitation acceptance, each on its own DB connection."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AppError
from app.models import User, WorkspaceMember, WorkspaceRole
from app.services import workspaces as workspace_service
from app.services.auth import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidRefreshTokenError,
    IssuedTokens,
    RefreshTokenReusedError,
)
from app.services.authorization import WorkspaceAccess
from app.services.workspaces import AlreadyMemberError, InvitationNotFoundError
from tests.conftest import make_settings

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def auth() -> AuthService:
    return AuthService(make_settings())


async def _register(engine: AsyncEngine, auth: AuthService, email: str) -> IssuedTokens:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await auth.register(
            session, email=email, password=PASSWORD, workspace_name="Personal workspace"
        )


async def _outcomes(*coroutines: Any) -> list[Any]:
    return list(await asyncio.gather(*coroutines, return_exceptions=True))


async def _count(engine: AsyncEngine, statement: Any) -> int:
    async with AsyncSession(engine) as session:
        count: int = (await session.execute(statement)).scalar_one()
        return count


async def test_simultaneous_signups_with_one_email_create_one_account(
    engine: AsyncEngine, auth: AuthService
) -> None:
    results = await _outcomes(*(_register(engine, auth, "race@example.com") for _ in range(5)))

    assert sum(isinstance(r, IssuedTokens) for r in results) == 1
    assert sum(isinstance(r, EmailAlreadyRegisteredError) for r in results) == 4
    assert await _count(engine, select(func.count()).select_from(User)) == 1
    assert await _count(engine, select(func.count()).select_from(WorkspaceMember)) == 1


async def test_simultaneous_refreshes_with_one_token_cannot_both_succeed(
    engine: AsyncEngine, auth: AuthService
) -> None:
    issued = await _register(engine, auth, "tabs@example.com")
    assert issued.csrf_token is not None

    async def attempt() -> IssuedTokens:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await auth.refresh(
                session, refresh_token=issued.refresh_token, csrf_header=issued.csrf_token
            )

    results = await _outcomes(attempt(), attempt())

    # Row locks serialize the two; the loser presents a spent token, which is treated as theft.
    assert sum(isinstance(r, IssuedTokens) for r in results) == 1
    assert sum(isinstance(r, RefreshTokenReusedError) for r in results) == 1
    winner = next(r for r in results if isinstance(r, IssuedTokens))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(InvalidRefreshTokenError):
            await auth.refresh(
                session, refresh_token=winner.refresh_token, csrf_header=issued.csrf_token
            )


async def test_simultaneous_acceptance_of_one_invitation_joins_once(
    engine: AsyncEngine, auth: AuthService
) -> None:
    owner = await _register(engine, auth, "owner@example.com")
    invitee = await _register(engine, auth, "invitee@example.com")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        workspace_id = (
            await session.execute(
                select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == owner.user.id)
            )
        ).scalar_one()
        created = await workspace_service.create_invitation(
            session,
            WorkspaceAccess(
                workspace_id=workspace_id, user_id=owner.user.id, role=WorkspaceRole.OWNER
            ),
            email="invitee@example.com",
            role=WorkspaceRole.MEMBER,
            ttl=timedelta(hours=1),
        )

    async def attempt() -> object:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await workspace_service.accept_invitation(
                session, user=invitee.user, token=created.token
            )

    results = await _outcomes(attempt(), attempt(), attempt())

    assert sum(not isinstance(r, BaseException) for r in results) == 1
    failures = [r for r in results if isinstance(r, BaseException)]
    assert all(isinstance(f, InvitationNotFoundError | AlreadyMemberError) for f in failures)
    assert all(isinstance(f, AppError) for f in failures)
    in_workspace = select(func.count()).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == invitee.user.id
    )
    assert await _count(engine, in_workspace) == 1
