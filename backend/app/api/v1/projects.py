"""Software project progress (ADR-0049, ADR-0051), under
/workspaces/{workspace_id}/workflows/{workflow_id}: workspace applications and integration runs.
VIEWERs read; only platform jobs write."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, workspace_role
from app.models import WorkspaceRole
from app.schemas.projects import (
    IntegrationResponse,
    PackageResponse,
    WorkspaceApplicationResponse,
)
from app.services import projects as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(prefix="/workspaces/{workspace_id}/workflows/{workflow_id}", tags=["projects"])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Admin = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.ADMIN))]


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


@router.get("/packages")
async def list_packages(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> list[PackageResponse]:
    """Packaging attempts, newest first. Download archives via the artifacts API."""
    return await service.list_packages(session, access, workflow_id)


@router.post("/packages", status_code=status.HTTP_202_ACCEPTED)
async def request_package(workflow_id: uuid.UUID, access: Admin, session: DbSession) -> Response:
    """Queues a package of the project's current commit (ADMIN). Labelled PARTIAL unless that
    commit passed the integration gate; verified projects are packaged automatically."""
    await service.request_partial_package(session, access, workflow_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)
