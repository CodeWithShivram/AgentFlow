"""Stored artifacts of a workflow (ADR-0047), under
/workspaces/{workspace_id}/workflows/{workflow_id}/artifacts.

VIEWERs list and download; ADMINs delete. There is no upload: only platform jobs create
artifacts. Downloads are verified against the recorded SHA-256 and always served as
attachments that browsers must not sniff or render.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse

from app.api.deps import DbSession, workspace_role
from app.artifacts.storage import LocalArtifactStorage
from app.models import WorkspaceRole
from app.schemas.artifacts import ArtifactResponse
from app.services import artifacts as service
from app.services.authorization import WorkspaceAccess

router = APIRouter(
    prefix="/workspaces/{workspace_id}/workflows/{workflow_id}/artifacts", tags=["artifacts"]
)

Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Admin = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.ADMIN))]


def get_artifact_storage(request: Request) -> LocalArtifactStorage:
    storage: LocalArtifactStorage = request.app.state.artifact_storage
    return storage


Storage = Annotated[LocalArtifactStorage, Depends(get_artifact_storage)]


@router.get("")
async def list_artifacts(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> list[ArtifactResponse]:
    """The workflow's available artifacts, newest first (at most 200)."""
    artifacts = await service.list_for_workflow(session, access, workflow_id)
    return [ArtifactResponse.of(artifact) for artifact in artifacts]


@router.get("/{artifact_id}")
async def get_artifact(
    workflow_id: uuid.UUID, artifact_id: uuid.UUID, access: Viewer, session: DbSession
) -> ArtifactResponse:
    return ArtifactResponse.of(
        await service.get_artifact(session, access, workflow_id, artifact_id)
    )


@router.get("/{artifact_id}/download", response_class=FileResponse)
async def download_artifact(
    workflow_id: uuid.UUID,
    artifact_id: uuid.UUID,
    access: Viewer,
    session: DbSession,
    storage: Storage,
) -> FileResponse:
    artifact = await service.get_artifact(session, access, workflow_id, artifact_id)
    path = await service.verified_file(storage, artifact)
    return FileResponse(
        path,
        media_type=artifact.media_type,
        filename=artifact.filename,
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Checksum-SHA256": artifact.sha256,
        },
    )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    workflow_id: uuid.UUID,
    artifact_id: uuid.UUID,
    access: Admin,
    session: DbSession,
    storage: Storage,
) -> Response:
    """Deletes the stored file; the record stays as DELETED history."""
    await service.delete_artifact(session, access, storage, workflow_id, artifact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
