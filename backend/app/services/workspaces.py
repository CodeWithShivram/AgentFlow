"""Workspace reads, membership and invitations. Mutations require a WorkspaceAccess proof."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.tokens import hash_token, new_opaque_token
from app.models import User, Workspace, WorkspaceInvitation, WorkspaceMember, WorkspaceRole
from app.repositories import workspaces as repository
from app.services.authorization import WorkspaceAccess, WorkspaceNotFoundError, can_grant

logger = logging.getLogger("agentflow.workspaces")


class CannotGrantRoleError(AppError):
    status_code = 403
    code = "CANNOT_GRANT_ROLE"
    message = "You cannot grant this role."


class AlreadyMemberError(AppError):
    status_code = 409
    code = "ALREADY_A_MEMBER"
    message = "This user is already a member of the workspace."


class InvitationNotFoundError(AppError):
    # One error for unknown, expired, used or someone else's invitation: no oracle.
    status_code = 404
    code = "INVITATION_NOT_FOUND"
    message = "Invitation not found or no longer valid."


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: WorkspaceInvitation
    # Shown to the inviter once so it can be shared; only its hash is stored.
    token: str


async def get_workspace(session: AsyncSession, access: WorkspaceAccess) -> Workspace:
    workspace = await repository.get_workspace(session, access.workspace_id)
    if workspace is None:  # deleted between authorization and read
        raise WorkspaceNotFoundError()
    return workspace


async def create_invitation(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    email: str,
    role: WorkspaceRole,
    ttl: timedelta,
) -> CreatedInvitation:
    if not can_grant(access.role, role):
        raise CannotGrantRoleError()
    email = email.lower()
    if await repository.is_member_email(session, workspace_id=access.workspace_id, email=email):
        raise AlreadyMemberError()
    token = new_opaque_token()
    invitation = WorkspaceInvitation(
        id=uuid.uuid4(),
        workspace_id=access.workspace_id,
        email=email,
        role=role,
        token_hash=hash_token(token),
        invited_by_user_id=access.user_id,
        expires_at=datetime.now(UTC) + ttl,
    )
    session.add(invitation)
    await session.commit()
    logger.info(
        "invitation created",
        extra={"workspace_id": str(access.workspace_id), "invitation_id": str(invitation.id)},
    )
    return CreatedInvitation(invitation=invitation, token=token)


async def accept_invitation(
    session: AsyncSession, *, user: User, token: str
) -> tuple[Workspace, WorkspaceRole]:
    invitation = await repository.lock_invitation(session, hash_token(token))
    now = datetime.now(UTC)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at <= now
        or invitation.email != user.email.lower()
    ):
        raise InvitationNotFoundError()
    existing = await repository.get_member_role(
        session, workspace_id=invitation.workspace_id, user_id=user.id
    )
    if existing is not None:
        raise AlreadyMemberError()
    session.add(
        WorkspaceMember(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role)
    )
    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id
    try:
        await session.flush()
    except IntegrityError as exc:  # concurrent acceptance through another invitation
        await session.rollback()
        raise AlreadyMemberError() from exc
    workspace = await repository.get_workspace(session, invitation.workspace_id)
    if workspace is None:  # unreachable: the foreign key and row lock keep it alive
        raise InvitationNotFoundError()
    role = invitation.role
    await session.commit()
    logger.info(
        "invitation accepted",
        extra={"workspace_id": str(workspace.id), "user_id": str(user.id)},
    )
    return workspace, role
