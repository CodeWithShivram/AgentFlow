"""Local artifact storage (ADR-0047): key containment, atomic size-limited writes, verified
reads, safe file names and the storage-root settings."""

import hashlib
import io
import os
import sys
import uuid
from pathlib import Path

import pytest

from app.artifacts.records import media_type_for
from app.artifacts.storage import (
    ArtifactIntegrityError,
    ArtifactMissingError,
    ArtifactTooLargeError,
    LocalArtifactStorage,
    UnsafeArtifactPathError,
    safe_filename,
    storage_key,
)
from app.core.config import REPO_ROOT_ENV_FILE
from tests.conftest import make_settings

WS, ART = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def storage(tmp_path: Path) -> LocalArtifactStorage:
    return LocalArtifactStorage(tmp_path / "root", max_bytes=64)


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_put_stores_bytes_with_checksum_and_size(storage: LocalArtifactStorage) -> None:
    key = storage_key(WS, ART)
    stored = storage.put(key, io.BytesIO(b"hello"))
    assert stored.size_bytes == 5
    assert stored.sha256 == hashlib.sha256(b"hello").hexdigest()
    path = storage.verified_path(key, size_bytes=5, sha256=stored.sha256)
    assert path.read_bytes() == b"hello"
    assert path.parent.name == str(WS)


def test_put_refuses_an_existing_key(storage: LocalArtifactStorage) -> None:
    key = storage_key(WS, ART)
    storage.put(key, io.BytesIO(b"a"))
    with pytest.raises(UnsafeArtifactPathError):
        storage.put(key, io.BytesIO(b"b"))


def test_oversized_writes_leave_no_file(storage: LocalArtifactStorage, tmp_path: Path) -> None:
    with pytest.raises(ArtifactTooLargeError):
        storage.put(storage_key(WS, ART), io.BytesIO(b"x" * 65))
    assert _files(tmp_path / "root") == []


@pytest.mark.parametrize(
    "key",
    [
        "../etc/passwd",
        f"{WS}/../{ART}",
        f"{WS}/{ART}/x",
        f"{WS}\\{ART}",
        f"/{WS}/{ART}",
        f"C:/{WS}/{ART}",
        f"{str(WS).upper()}/{ART}",
        f"{WS}/{ART}.zip",
        "",
    ],
)
def test_keys_must_be_platform_uuids(storage: LocalArtifactStorage, key: str) -> None:
    with pytest.raises(UnsafeArtifactPathError):
        storage.put(key, io.BytesIO(b"a"))
    with pytest.raises(UnsafeArtifactPathError):
        storage.verified_path(key, size_bytes=1, sha256="0" * 64)


def test_reads_verify_size_and_checksum(storage: LocalArtifactStorage) -> None:
    key = storage_key(WS, ART)
    stored = storage.put(key, io.BytesIO(b"hello"))
    with pytest.raises(ArtifactIntegrityError):
        storage.verified_path(key, size_bytes=4, sha256=stored.sha256)
    with pytest.raises(ArtifactIntegrityError):
        storage.verified_path(key, size_bytes=5, sha256="0" * 64)
    assert storage.delete(key) is True
    assert storage.delete(key) is False
    with pytest.raises(ArtifactMissingError):
        storage.verified_path(key, size_bytes=5, sha256=stored.sha256)


def test_missing_root_is_missing_not_created_on_read(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "absent", max_bytes=10)
    with pytest.raises(ArtifactMissingError):
        storage.verified_path(storage_key(WS, ART), size_bytes=1, sha256="0" * 64)
    assert not (tmp_path / "absent").exists()


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and target.is_dir():
        # Junctions are Windows directory links that need no privilege.
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("creating symlinks needs a privilege this machine does not grant")


def test_symlinked_workspace_folder_is_refused(
    storage: LocalArtifactStorage, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.put(storage_key(WS, uuid.uuid4()), io.BytesIO(b"a"))  # creates the root
    other = uuid.uuid4()
    _symlink_or_skip(tmp_path / "root" / str(other), outside)
    with pytest.raises(UnsafeArtifactPathError):
        storage.put(storage_key(other, ART), io.BytesIO(b"a"))
    assert _files(outside) == []


def test_symlinked_file_is_refused(storage: LocalArtifactStorage, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    storage.put(storage_key(WS, uuid.uuid4()), io.BytesIO(b"a"))
    _symlink_or_skip(tmp_path / "root" / str(WS) / str(ART), secret)
    with pytest.raises(UnsafeArtifactPathError):
        storage.verified_path(
            storage_key(WS, ART), size_bytes=6, sha256=hashlib.sha256(b"secret").hexdigest()
        )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("project.zip", "project.zip"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\report.md", "report.md"),
        ("my project (final).zip", "my_project_final_.zip"),
        (".env", "env"),
        ("...", "artifact"),
        ("", "artifact"),
        ("a" * 200 + ".zip", "a" * 116 + ".zip"),
    ],
)
def test_safe_filename(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


def test_media_types_are_fixed_by_extension() -> None:
    assert media_type_for("project.zip") == "application/zip"
    assert media_type_for("REPORT.MD") == "text/markdown; charset=utf-8"
    assert media_type_for("page.html") == "application/octet-stream"
    assert media_type_for("image.svg") == "application/octet-stream"


def test_storage_root_must_be_absolute_and_outside_the_repository() -> None:
    with pytest.raises(ValueError, match="absolute"):
        make_settings(artifact_storage_root=Path("relative/artifacts"))
    with pytest.raises(ValueError, match="outside the repository"):
        make_settings(artifact_storage_root=REPO_ROOT_ENV_FILE.parent / "artifacts")
    assert make_settings().artifact_storage_root.is_absolute()
