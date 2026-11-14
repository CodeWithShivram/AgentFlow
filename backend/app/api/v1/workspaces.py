"""Workspace, membership and invitation endpoints.

Every route that names a workspace authorizes it through `workspace_role(...)`; non-members
receive 404 whatever ID they try.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentPrincipal, DbSession, RateLimiterDep, SettingsDep, workspace_role
from app.models import WorkspaceRole
from app.repositories import workspaces as repository
from app.schemas.workflows import FailedJobResponse
from app.schemas.workspaces import (
    AcceptedMembershipResponse,
    InvitationAcceptRequest,
    InvitationCreatedResponse,
    InvitationCreateRequest,
    MemberResponse,
    Page,
    WorkspaceResponse,
)
from app.services import background_jobs
from app.services import workspaces as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(tags=["workspaces"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
ViewerAccess = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
AdminAccess = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.ADMIN))]


@router.get("/workspaces")
async def list_workspaces(
    principal: CurrentPrincipal, session: DbSession, limit: Limit = 50, offset: Offset = 0
) -> Page[WorkspaceResponse]:
    """Workspaces the caller belongs to."""
    rows, total = await repository.list_for_user(
        session, user_id=principal.user_id, limit=limit, offset=offset
    )
    items = [
        WorkspaceResponse(
            id=w.id, name=w.name, owner_id=w.owner_id, role=role, created_at=w.created_at
        )
        for w, role in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/workspaces/{workspace_id}")
async def get_workspace(access: ViewerAccess, session: DbSession) -> WorkspaceResponse:
    workspace = await service.get_workspace(session, access)
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        owner_id=workspace.owner_id,
        role=access.role,
        created_at=workspace.created_at,
    )


@router.get("/workspaces/{workspace_id}/members")
async def list_members(
    access: ViewerAccess, session: DbSession, limit: Limit = 50, offset: Offset = 0
) -> Page[MemberResponse]:
    rows, total = await repository.list_members(
        session, workspace_id=access.workspace_id, limit=limit, offset=offset
    )
    items = [
        MemberResponse(user_id=u.id, email=u.email, role=m.role, joined_at=m.created_at)
        for m, u in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/workspaces/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: InvitationCreateRequest,
    access: AdminAccess,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> InvitationCreatedResponse:
    """ADMIN or OWNER. Owners may invite ADMIN, MEMBER or VIEWER; admins MEMBER or VIEWER."""
    await limiter.hit("invite:user", str(access.user_id), limit=settings.auth_requests_per_minute)
    created = await service.create_invitation(
        session,
        access,
        email=body.email,
        role=WorkspaceRole(body.role),
        ttl=timedelta(seconds=settings.workspace_invitation_ttl_seconds),
    )
    invitation = created.invitation
    return InvitationCreatedResponse(
        id=invitation.id,
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invitation_token=created.token,
    )


@router.post("/invitations/accept")
async def accept_invitation(
    body: InvitationAcceptRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
) -> AcceptedMembershipResponse:
    """Joins the workspace. The invitation must be addressed to the caller's email."""
    await limiter.hit(
        "invite-accept:user", str(principal.user_id), limit=settings.auth_login_attempts_per_minute
    )
    workspace, role = await service.accept_invitation(
        session, user=principal.user, token=body.token.get_secret_value()
    )
    return AcceptedMembershipResponse(
        workspace_id=workspace.id, workspace_name=workspace.name, role=role
    )


@router.get("/workspaces/{workspace_id}/failed-jobs")
async def list_workspace_failed_jobs(
    access: AdminAccess, session: DbSession, limit: Limit = 50, offset: Offset = 0
) -> Page[FailedJobResponse]:
    """Every background job of the workspace the relay gave up on (ADMIN, ADR-0027)."""
    events, total = await background_jobs.list_for_workspace(
        session, access, limit=limit, offset=offset
    )
    return Page(
        items=[background_jobs.failed_job_response(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )
