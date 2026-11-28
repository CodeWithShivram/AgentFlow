"""Purging a deleted workflow's files (Step 9.11, ADR-0058).

The API soft-deletes the workflow and writes `workflow.purge`. This job marks the workflow's
AVAILABLE artifacts DELETED (kept as history), commits, then removes their bytes and the project
workspace. Every step is idempotent; a removal that fails leaves unreachable files for the
orphan sweep (ADR-0047) and is logged, never retried forever.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.storage import ArtifactStorageError, LocalArtifactStorage
from app.execution import outbox
from app.models import Artifact, ArtifactStatus, Workflow
from app.projects.workspace import ProjectWorkspaces, WorkspaceError

logger = logging.getLogger("agentflow.projects.purge")
CONSUMER = "projects.purge"


def run(
    session: Session,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> str:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return "duplicate"
    workflow = session.scalars(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    ).one_or_none()
    if workflow is None or workflow.deleted_at is None:
        session.commit()
        return "stale"
    artifacts = list(
        session.scalars(
            select(Artifact)
            .where(
                Artifact.workspace_id == workspace_id,
                Artifact.workflow_id == workflow_id,
                Artifact.status == ArtifactStatus.AVAILABLE,
            )
            .with_for_update()
        )
    )
    now = datetime.now(UTC)
    for artifact in artifacts:
        artifact.status = ArtifactStatus.DELETED
        artifact.deleted_at = now
    keys = [artifact.storage_key for artifact in artifacts]
    session.commit()

    left = 0
    for key in keys:
        try:
            storage.delete(key)
        except (OSError, ArtifactStorageError):
            left += 1
    try:
        workspaces.remove(workspace_id, workflow_id)
    except (OSError, WorkspaceError):
        left += 1
    logger.info(
        "workflow files purged",
        extra={"workflow_id": str(workflow_id), "artifacts": len(keys), "left_behind": left},
    )
    return "purged" if not left else "partial"
