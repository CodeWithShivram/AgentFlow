"""Local artifact storage (ADR-0047): bytes on the local filesystem, under one configured root
outside the repository. Metadata and checksums live in PostgreSQL (`app.models.Artifact`).

Rules:
- Keys are `<workspace_id>/<artifact_id>` built from UUIDs by the platform, never from names a
  person, agent or model chose. Every key is parsed again, resolved, and must stay inside the
  root; symlinked roots, folders or files are refused.
- Writes stream to a temporary file in the destination folder, enforce the size limit while
  copying, hash with SHA-256, flush to disk and then rename atomically. A failed write leaves
  nothing behind.
- Reads verify size and SHA-256 against the stored metadata before the bytes are served.

Errors carry fixed codes and messages; they never include paths, which would reveal the host's
layout.
"""

import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(rf"^({_UUID})/({_UUID})$")
_UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
FILENAME_MAX_LENGTH = 120


class ArtifactStorageError(Exception):
    """A storage failure with a fixed, safe code."""

    code = "ARTIFACT_STORAGE_ERROR"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)
        if code:
            self.code = code


class ArtifactTooLargeError(ArtifactStorageError):
    code = "ARTIFACT_TOO_LARGE"


class ArtifactMissingError(ArtifactStorageError):
    code = "ARTIFACT_MISSING"


class ArtifactIntegrityError(ArtifactStorageError):
    code = "ARTIFACT_INTEGRITY_FAILED"


class UnsafeArtifactPathError(ArtifactStorageError):
    code = "ARTIFACT_PATH_REFUSED"


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


def storage_key(workspace_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"{workspace_id}/{artifact_id}"


def safe_filename(name: str, *, default: str = "artifact") -> str:
    """A display/download file name: letters, digits, `.`, `_` and `-` only, no leading dot,
    at most 120 characters, keeping the extension when it has to shorten the name."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_FILENAME_CHARACTERS.sub("_", base).strip("._-")
    if not cleaned:
        return default
    if len(cleaned) > FILENAME_MAX_LENGTH:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and 0 < len(extension) <= 10 and stem:
            cleaned = f"{stem[: FILENAME_MAX_LENGTH - len(extension) - 1]}.{extension}"
        else:
            cleaned = cleaned[:FILENAME_MAX_LENGTH]
    return cleaned


def _is_link(path: Path) -> bool:
    # Junctions are Windows' directory links; `is_junction` exists from Python 3.12.
    return path.is_symlink() or path.is_junction()


class LocalArtifactStorage:
    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self._configured_root = root
        self.max_bytes = max_bytes

    def _root(self, *, create: bool) -> Path:
        root = self._configured_root
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not root.is_dir():
            raise ArtifactMissingError()
        if _is_link(root):
            raise UnsafeArtifactPathError()
        return root.resolve(strict=True)

    def _path(self, key: str, *, create_parent: bool) -> Path:
        match = _KEY.fullmatch(key)
        if match is None:
            raise UnsafeArtifactPathError()
        try:
            workspace, artifact = uuid.UUID(match[1]), uuid.UUID(match[2])
        except ValueError:
            raise UnsafeArtifactPathError() from None
        if key != storage_key(workspace, artifact):
            raise UnsafeArtifactPathError()
        root = self._root(create=create_parent)
        folder = root / match[1]
        if create_parent:
            folder.mkdir(exist_ok=True, mode=0o700)
        if folder.exists() and (_is_link(folder) or not folder.is_dir()):
            raise UnsafeArtifactPathError()
        path = folder / match[2]
        if _is_link(path):
            raise UnsafeArtifactPathError()
        # Defence in depth: the resolved location must still be inside the resolved root.
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(root) or resolved.parent != root / match[1]:
            raise UnsafeArtifactPathError()
        return path

    def put(self, key: str, source: BinaryIO) -> StoredObject:
        """Stores the stream under `key` (which must not exist yet) and returns its checksum."""
        path = self._path(key, create_parent=True)
        if path.exists():
            raise UnsafeArtifactPathError()
        digest = hashlib.sha256()
        size = 0
        descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ArtifactTooLargeError()
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return StoredObject(key=key, size_bytes=size, sha256=digest.hexdigest())

    def verified_path(self, key: str, *, size_bytes: int, sha256: str) -> Path:
        """The stored file, after checking it is a regular file with the recorded size and
        SHA-256. Reads the whole file, so call it off the event loop."""
        path = self._path(key, create_parent=False)
        if not path.is_file():
            raise ArtifactMissingError()
        if path.stat().st_size != size_bytes:
            raise ArtifactIntegrityError()
        digest = hashlib.sha256()
        with path.open("rb") as stored:
            while chunk := stored.read(CHUNK_SIZE):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise ArtifactIntegrityError()
        return path

    def delete(self, key: str) -> bool:
        """Removes the stored bytes; False when they were already gone."""
        try:
            path = self._path(key, create_parent=False)
        except ArtifactMissingError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True
