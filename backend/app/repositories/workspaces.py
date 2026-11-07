"""Workspace, membership and invitation queries. Every query is scoped by workspace or user."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workspace, WorkspaceInvitation, WorkspaceMember, WorkspaceRole


async def get_member_role(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> WorkspaceRole | None:
    statement = select(WorkspaceMember.role).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def list_for_user(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[tuple[Workspace, WorkspaceRole]], int]:
    scope = WorkspaceMember.user_id == user_id
    rows = await session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(scope)
        .order_by(Workspace.created_at, Workspace.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(WorkspaceMember).where(scope))
    return [(row[0], row[1]) for row in rows], total.scalar_one()


async def get_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace | None:
    """Callers must have authorized access to `workspace_id` first."""
    return await session.get(Workspace, workspace_id)


async def list_members(
    session: AsyncSession, *, workspace_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[tuple[WorkspaceMember, User]], int]:
    scope = WorkspaceMember.workspace_id == workspace_id
    rows = await session.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(scope)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(WorkspaceMember).where(scope))
    return [(row[0], row[1]) for row in rows], total.scalar_one()


async def is_member_email(session: AsyncSession, *, workspace_id: uuid.UUID, email: str) -> bool:
    statement = (
        select(WorkspaceMember.id)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id, func.lower(User.email) == email)
    )
    return (await session.execute(statement)).first() is not None


async def lock_invitation(session: AsyncSession, token_hash: str) -> WorkspaceInvitation | None:
    statement = (
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()
