"""Usage and cost analytics (ADR-0041), under /workspaces/{workspace_id}/analytics.

VIEWERs and above read them, like a workflow's costs. Ranges default to the last 30 days and
span at most 366 days; rules live in app.services.analytics.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, workspace_role
from app.models import WorkspaceRole
from app.schemas.analytics import (
    CostBreakdownResponse,
    GroupBy,
    OverviewResponse,
    TokenBreakdownResponse,
    WorkflowAnalyticsResponse,
)
from app.services import analytics as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(prefix="/workspaces/{workspace_id}/analytics", tags=["analytics"])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Start = Annotated[
    datetime | None, Query(alias="from", description="Start (inclusive), with a time zone.")
]
End = Annotated[
    datetime | None, Query(alias="to", description="End (exclusive), with a time zone.")
]
Limit = Annotated[int, Query(ge=1, le=500)]


@router.get("/overview")
async def overview(
    access: Viewer, session: DbSession, start: Start = None, end: End = None
) -> OverviewResponse:
    """Model calls, tokens and costs in the range, with how many workflows and agents used them."""
    return await service.overview(session, access, service.resolve_range(start, end))


@router.get("/costs")
async def costs(
    access: Viewer,
    session: DbSession,
    group_by: GroupBy = "workflow",
    workflow_id: uuid.UUID | None = None,
    start: Start = None,
    end: End = None,
    limit: Limit = 100,
) -> CostBreakdownResponse:
    """Calls, tokens and costs per group; actual, estimated and mock costs stay apart."""
    return await service.costs(
        session,
        access,
        service.resolve_range(start, end),
        group_by,
        workflow_id=workflow_id,
        limit=limit,
    )


@router.get("/tokens")
async def tokens(
    access: Viewer,
    session: DbSession,
    group_by: GroupBy = "workflow",
    workflow_id: uuid.UUID | None = None,
    start: Start = None,
    end: End = None,
    limit: Limit = 100,
) -> TokenBreakdownResponse:
    """Calls and tokens per group."""
    return await service.tokens(
        session,
        access,
        service.resolve_range(start, end),
        group_by,
        workflow_id=workflow_id,
        limit=limit,
    )


@router.get("/agents")
async def agents(
    access: Viewer, session: DbSession, start: Start = None, end: End = None, limit: Limit = 100
) -> CostBreakdownResponse:
    """Per agent; calls no agent made are grouped as "platform" by purpose."""
    return await service.costs(
        session, access, service.resolve_range(start, end), "agent", workflow_id=None, limit=limit
    )


@router.get("/workflows/{workflow_id}")
async def workflow(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession, limit: Limit = 100
) -> WorkflowAnalyticsResponse:
    """One workflow's whole history by task, agent and model."""
    return await service.workflow(session, access, workflow_id, limit)
