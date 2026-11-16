"""Requests that need a person (ADR-0035), under /workspaces/{workspace_id}/interventions.

VIEWERs read, MEMBERs answer clarifications, ADMINs approve or reject. Rules live in
app.services.interventions.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, workspace_role
from app.models import InterventionStatus, WorkspaceRole
from app.schemas.interventions import AnswerRequest, InterventionResponse
from app.schemas.workspaces import Page
from app.services import interventions as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(prefix="/workspaces/{workspace_id}/interventions", tags=["interventions"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Member = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.MEMBER))]
Admin = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.ADMIN))]


@router.get("")
async def list_interventions(
    access: Viewer,
    session: DbSession,
    status: InterventionStatus | None = None,
    workflow_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[InterventionResponse]:
    """The workspace's requests, newest first."""
    items, total = await service.list_interventions(
        session, access, status=status, workflow_id=workflow_id, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{intervention_id}")
async def get_intervention(
    intervention_id: uuid.UUID, access: Viewer, session: DbSession
) -> InterventionResponse:
    return await service.get_intervention(session, access, intervention_id)


@router.post("/{intervention_id}/answer")
async def answer_intervention(
    intervention_id: uuid.UUID, body: AnswerRequest, access: Member, session: DbSession
) -> InterventionResponse:
    """Answers a clarification; the answer reaches the next plan or replan draft."""
    return await service.answer(session, access, intervention_id, body.answer)


@router.post("/{intervention_id}/approve")
async def approve_intervention(
    intervention_id: uuid.UUID, access: Admin, session: DbSession
) -> InterventionResponse:
    return await service.decide(session, access, intervention_id, "approve")


@router.post("/{intervention_id}/reject")
async def reject_intervention(
    intervention_id: uuid.UUID, access: Admin, session: DbSession
) -> InterventionResponse:
    return await service.decide(session, access, intervention_id, "reject")
