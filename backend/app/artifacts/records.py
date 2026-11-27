"""Creating artifacts (ADR-0047). Platform code only: worker jobs (packaging, Step 9.6) store
bytes and record metadata here. There is no upload API.

The bytes are written first, then the row is added to the caller's transaction. If the
caller's transaction fails, `discard` removes the bytes; a crash between the write and the
commit leaves an unreferenced file under the root, which no download can reach (downloads start
from a row). Sweeping such files is tracked in ADR-0047.
"""

import uuid
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.artifacts.storage import LocalArtifactStorage, safe_filename, storage_key
from app.core.config import Settings
from app.models import Artifact, ArtifactKind

_MEDIA_TYPES = {
    ".zip": "application/zip",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
}
DEFAULT_MEDIA_TYPE = "application/octet-stream"


def storage_from_settings(settings: Settings) -> LocalArtifactStorage:
    return LocalArtifactStorage(
        settings.artifact_storage_root, max_bytes=settings.artifact_max_bytes
    )


def media_type_for(filename: str) -> str:
    """A fixed media type by extension. Unknown types are served as opaque bytes, never as a
    type a browser would render."""
    for extension, media_type in _MEDIA_TYPES.items():
        if filename.lower().endswith(extension):
            return media_type
    return DEFAULT_MEDIA_TYPE


def store_artifact(
    session: Session,
    storage: LocalArtifactStorage,
    *,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    kind: ArtifactKind,
    filename: str,
    source: BinaryIO,
    task_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
) -> Artifact:
    """Stores the bytes and adds (and flushes) the metadata row in the caller's transaction."""
    artifact_id = uuid.uuid4()
    name = safe_filename(filename)
    stored = storage.put(storage_key(workspace_id, artifact_id), source)
    try:
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            task_id=task_id,
            execution_id=execution_id,
            kind=kind,
            filename=name,
            media_type=media_type_for(name),
            storage_key=stored.key,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        session.add(artifact)
        session.flush()
    except BaseException:
        storage.delete(stored.key)
        raise
    return artifact


def discard(storage: LocalArtifactStorage, artifact: Artifact) -> None:
    """Removes the bytes of an artifact whose transaction did not commit."""
    storage.delete(artifact.storage_key)
