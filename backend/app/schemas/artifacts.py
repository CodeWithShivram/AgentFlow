"""Artifact API models (ADR-0047). The storage key and the storage location are never exposed."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models import Artifact, ArtifactKind


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    kind: ArtifactKind
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime

    @classmethod
    def of(cls, artifact: Artifact) -> "ArtifactResponse":
        return cls(
            id=artifact.id,
            workflow_id=artifact.workflow_id,
            task_id=artifact.task_id,
            execution_id=artifact.execution_id,
            kind=artifact.kind,
            filename=artifact.filename,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            created_at=artifact.created_at,
        )
