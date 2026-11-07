"""Centralized workspace authorization. Every workspace-scoped operation goes through here.

Roles are read from the database on every request; tokens never carry them.
Non-members get 404 (the workspace's existence is not revealed); members whose role is too
low get 403.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import WorkspaceRole
from app.repositories import workspaces as repository

ROLE_RANK = {
    WorkspaceRole.VIEWER: 1,
    WorkspaceRole.MEMBER: 2,
    WorkspaceRole.ADMIN: 3,
    WorkspaceRole.OWNER: 4,
}


class WorkspaceNotFoundError(AppError):
    status_code = 404
    code = "WORKSPACE_NOT_FOUND"
    message = "Workspace not found."


class InsufficientRoleError(AppError):
    status_code = 403
    code = "INSUFFICIENT_ROLE"
    message = "Your role in this workspace does not allow this action."


@dataclass(frozen=True)
class WorkspaceAccess:
    """Proof that `user_id` holds `role` in `workspace_id`, checked against the database."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: WorkspaceRole


def role_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def can_grant(granter: WorkspaceRole, role: WorkspaceRole) -> bool:
    """Owners may grant ADMIN and below; admins may grant only roles below their own."""
    if role == WorkspaceRole.OWNER:
        return False
    if granter == WorkspaceRole.OWNER:
        return True
    return ROLE_RANK[role] < ROLE_RANK[granter]


async def authorize_workspace(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    minimum: WorkspaceRole,
) -> WorkspaceAccess:
    role = await repository.get_member_role(session, workspace_id=workspace_id, user_id=user_id)
    if role is None:
        raise WorkspaceNotFoundError()
    if not role_at_least(role, minimum):
        raise InsufficientRoleError()
    return WorkspaceAccess(workspace_id=workspace_id, user_id=user_id, role=role)
