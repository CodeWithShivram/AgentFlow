"""Software project progress (ADR-0049, ADR-0051), under
/workspaces/{workspace_id}/workflows/{workflow_id}: workspace applications and integration runs.
VIEWERs read; only platform jobs write."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, workspace_role
from app.models import WorkspaceRole
from app.schemas.projects import IntegrationResponse, WorkspaceApplicationResponse
from app.services import projects as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(prefix="/workspaces/{workspace_id}/workflows/{workflow_id}", tags=["projects"])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]


@router.get("/integrations")
async def list_integrations(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> list[IntegrationResponse]:
    """Integration gate runs, newest first (at most 100)."""
    return await service.list_integrations(session, access, workflow_id)


@router.get("/workspace-applications")
async def list_workspace_applications(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> list[WorkspaceApplicationResponse]:
    """Accepted attempts applied (or not) to the project workspace, newest first."""
    return await service.list_applications(session, access, workflow_id)
