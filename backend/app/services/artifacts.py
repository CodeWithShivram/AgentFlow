"""Reading, downloading and deleting stored artifacts (ADR-0047).

Every lookup goes through the workflow first (`get_workflow`, scoped to the caller's workspace)
and then filters by workspace and workflow; row-level security is the second layer. Downloads
verify the stored file's size and SHA-256 before serving it. Deleting marks the row DELETED
(kept as history) in the caller's transaction; the route removes the bytes after the commit.
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.storage import (
    ArtifactIntegrityError,
    ArtifactMissingError,
    ArtifactStorageError,
    LocalArtifactStorage,
)
from app.core.errors import AppError
from app.models import Artifact, ArtifactStatus
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow

LIST_LIMIT = 200
logger = logging.getLogger("agentflow.artifacts")


class ArtifactNotFoundError(AppError):
    status_code = 404
    code = "ARTIFACT_NOT_FOUND"
    message = "No artifact with this id exists in this workflow."


class ArtifactUnavailableError(AppError):
    status_code = 410
    code = "ARTIFACT_UNAVAILABLE"
    message = "The stored file for this artifact is no longer available."


class ArtifactIntegrityFailedError(AppError):
    status_code = 409
    code = "ARTIFACT_INTEGRITY_FAILED"
    message = "The stored file does not match its recorded checksum, so it was not served."


async def list_for_workflow(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[Artifact]:
    workflow = await get_workflow(session, access, workflow_id)
    rows = await session.execute(
        select(Artifact)
        .where(
            Artifact.workspace_id == workflow.workspace_id,
            Artifact.workflow_id == workflow.id,
            Artifact.status == ArtifactStatus.AVAILABLE,
        )
        .order_by(Artifact.created_at.desc(), Artifact.id)
        .limit(LIST_LIMIT)
    )
    return list(rows.scalars())


async def get_artifact(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Artifact:
    workflow = await get_workflow(session, access, workflow_id)
    query = select(Artifact).where(
        Artifact.workspace_id == workflow.workspace_id,
        Artifact.workflow_id == workflow.id,
        Artifact.id == artifact_id,
        Artifact.status == ArtifactStatus.AVAILABLE,
    )
    if for_update:
        query = query.with_for_update()
    artifact = (await session.execute(query)).scalar_one_or_none()
    if artifact is None:
        raise ArtifactNotFoundError()
    return artifact


async def verified_file(storage: LocalArtifactStorage, artifact: Artifact) -> Path:
    """The artifact's file after checking its size and SHA-256 (off the event loop)."""

    def check() -> Path:
        return storage.verified_path(
            artifact.storage_key, size_bytes=artifact.size_bytes, sha256=artifact.sha256
        )

    try:
        return await anyio.to_thread.run_sync(check)
    except ArtifactIntegrityError:
        raise ArtifactIntegrityFailedError() from None
    except (ArtifactMissingError, ArtifactStorageError):
        raise ArtifactUnavailableError() from None


async def delete_artifact(
    session: AsyncSession,
    access: WorkspaceAccess,
    storage: LocalArtifactStorage,
    workflow_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> None:
    """Marks the artifact DELETED (kept as history), commits, then removes its bytes. If the
    removal fails after the commit, the file is unreachable (downloads start from an AVAILABLE
    row) and is left for the orphan sweep (ADR-0047)."""
    artifact = await get_artifact(session, access, workflow_id, artifact_id, for_update=True)
    artifact.status = ArtifactStatus.DELETED
    artifact.deleted_at = datetime.now(UTC)
    artifact.deleted_by_user_id = access.user_id
    await session.commit()
    key = artifact.storage_key
    try:
        await anyio.to_thread.run_sync(storage.delete, key)
    except (OSError, ArtifactStorageError):
        logger.warning("artifact bytes not removed", extra={"artifact_id": str(artifact.id)})
