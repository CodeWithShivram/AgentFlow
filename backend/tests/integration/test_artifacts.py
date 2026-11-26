"""Stored artifacts (ADR-0047): platform-created records, workspace-scoped listing and
verified downloads, admin deletion, row-level security and the immutability trigger."""

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.records import store_artifact
from app.artifacts.storage import ArtifactTooLargeError, LocalArtifactStorage
from app.core.config import Settings
from app.db.sync_session import session_scope
from app.models import Artifact, ArtifactKind, ArtifactStatus
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    csrf,
    my_workspace,
    new_email,
    register,
    sql,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
CONTENT = b"PK\x05\x06" + b"\x00" * 18  # an empty ZIP archive


@pytest.fixture
def storage(tmp_path: Path) -> LocalArtifactStorage:
    return LocalArtifactStorage(tmp_path / "artifacts", max_bytes=1024 * 1024)


@pytest.fixture
def artifact_settings(api_settings: Settings, storage: LocalArtifactStorage) -> Settings:
    return api_settings.model_copy(
        update={"artifact_storage_root": storage._configured_root, "artifact_max_bytes": 1 << 20}
    )


@pytest.fixture
def owner(new_client: ClientFactory, artifact_settings: Settings) -> TestClient:
    client = new_client(artifact_settings)
    assert register(client, new_email()).status_code == 201
    return client


def _workflow(client: TestClient) -> str:
    response = client.post(
        f"{API}/workspaces/{my_workspace(client)}/workflows",
        json={"prompt": "Build a small command-line tool."},
        headers=csrf(client),
    )
    assert response.status_code == 201
    workflow_id: str = response.json()["id"]
    return workflow_id


def _store(
    factory: Factory,
    storage: LocalArtifactStorage,
    workspace_id: str,
    workflow_id: str,
    *,
    content: bytes = CONTENT,
    filename: str = "project.zip",
) -> Artifact:
    with session_scope(factory) as session:
        artifact = store_artifact(
            session,
            storage,
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            kind=ArtifactKind.PROJECT_ARCHIVE,
            filename=filename,
            source=io.BytesIO(content),
        )
        session.commit()
    return artifact


def _url(client: TestClient, workflow_id: str, suffix: str = "") -> str:
    return f"{API}/workspaces/{my_workspace(client)}/workflows/{workflow_id}/artifacts{suffix}"


def test_worker_stores_and_members_list_and_download(
    owner: TestClient,
    new_client: ClientFactory,
    worker_factory: Factory,
    storage: LocalArtifactStorage,
    artifact_settings: Settings,
) -> None:
    workflow_id = _workflow(owner)
    artifact = _store(
        worker_factory, storage, my_workspace(owner), workflow_id, filename="../my project?.zip"
    )
    assert artifact.filename == "my_project_.zip"
    assert artifact.media_type == "application/zip"

    listed = owner.get(_url(owner, workflow_id))
    assert listed.status_code == 200
    body = listed.json()
    assert [item["id"] for item in body] == [str(artifact.id)]
    assert body[0]["size_bytes"] == len(CONTENT)
    assert "storage_key" not in body[0]

    viewer = member(new_client, owner, "VIEWER", artifact_settings)
    download = viewer.get(_url(owner, workflow_id, f"/{artifact.id}/download"))
    assert download.status_code == 200
    assert download.content == CONTENT
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["x-checksum-sha256"] == artifact.sha256
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "my_project_.zip" in download.headers["content-disposition"]


def test_other_workspaces_cannot_see_or_download(
    owner: TestClient,
    new_client: ClientFactory,
    worker_factory: Factory,
    storage: LocalArtifactStorage,
    artifact_settings: Settings,
) -> None:
    workflow_id = _workflow(owner)
    artifact = _store(worker_factory, storage, my_workspace(owner), workflow_id)
    stranger = new_client(artifact_settings)
    assert register(stranger, new_email()).status_code == 201

    # Their own workspace id with someone else's workflow: not found.
    url = f"{API}/workspaces/{my_workspace(stranger)}/workflows/{workflow_id}/artifacts"
    assert stranger.get(url).status_code == 404
    assert stranger.get(f"{url}/{artifact.id}/download").status_code == 404
    # The owner's workspace id: not a member, so not found either.
    assert stranger.get(_url(owner, workflow_id, f"/{artifact.id}/download")).status_code == 404


def test_tampered_file_is_refused(
    owner: TestClient, worker_factory: Factory, storage: LocalArtifactStorage
) -> None:
    workflow_id = _workflow(owner)
    artifact = _store(worker_factory, storage, my_workspace(owner), workflow_id)
    stored = storage._configured_root / artifact.storage_key
    stored.write_bytes(CONTENT[:-1] + b"\x01")

    response = owner.get(_url(owner, workflow_id, f"/{artifact.id}/download"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ARTIFACT_INTEGRITY_FAILED"

    stored.unlink()
    response = owner.get(_url(owner, workflow_id, f"/{artifact.id}/download"))
    assert response.status_code == 410
    assert str(storage._configured_root) not in response.text


def test_only_admins_delete_and_history_is_kept(
    owner: TestClient,
    new_client: ClientFactory,
    worker_factory: Factory,
    storage: LocalArtifactStorage,
    artifact_settings: Settings,
) -> None:
    workflow_id = _workflow(owner)
    artifact = _store(worker_factory, storage, my_workspace(owner), workflow_id)
    url = _url(owner, workflow_id, f"/{artifact.id}")

    other = member(new_client, owner, "MEMBER", artifact_settings)
    assert other.delete(url, headers=csrf(other)).status_code == 403

    assert owner.delete(url, headers=csrf(owner)).status_code == 204
    assert not (storage._configured_root / artifact.storage_key).exists()
    assert owner.get(url).status_code == 404
    assert owner.get(_url(owner, workflow_id)).json() == []
    rows = sql(
        artifact_settings,
        "SELECT status, deleted_by_user_id IS NOT NULL FROM artifacts WHERE id = :id",
        id=artifact.id,
    )
    assert [tuple(row) for row in rows] == [(ArtifactStatus.DELETED.value, True)]
    assert owner.delete(url, headers=csrf(owner)).status_code == 404


def test_oversized_artifacts_leave_nothing_behind(
    owner: TestClient, worker_factory: Factory, tmp_path: Path, artifact_settings: Settings
) -> None:
    small = LocalArtifactStorage(tmp_path / "small", max_bytes=4)
    workflow_id = _workflow(owner)
    with pytest.raises(ArtifactTooLargeError):
        _store(worker_factory, small, my_workspace(owner), workflow_id)
    assert [p for p in (tmp_path / "small").rglob("*") if p.is_file()] == []
    assert sql(artifact_settings, "SELECT count(*) FROM artifacts")[0][0] == 0


def test_failed_insert_removes_the_stored_bytes(
    owner: TestClient, worker_factory: Factory, storage: LocalArtifactStorage
) -> None:
    # A workflow that does not exist: the composite foreign key refuses the row.
    with pytest.raises(DBAPIError):
        _store(worker_factory, storage, my_workspace(owner), str(uuid.uuid4()))
    assert [p for p in storage._configured_root.rglob("*") if p.is_file()] == []


def test_identity_is_immutable_and_deletion_is_final(
    owner: TestClient, worker_factory: Factory, storage: LocalArtifactStorage
) -> None:
    workflow_id = _workflow(owner)
    artifact = _store(worker_factory, storage, my_workspace(owner), workflow_id)
    for statement in (
        "UPDATE artifacts SET sha256 = repeat('0', 64) WHERE id = :id",
        "UPDATE artifacts SET storage_key = 'x' WHERE id = :id",
        "UPDATE artifacts SET size_bytes = 1 WHERE id = :id",
    ):
        with session_scope(worker_factory) as session, pytest.raises(DBAPIError):
            session.execute(text(statement), {"id": artifact.id})
    with session_scope(worker_factory) as session:
        session.execute(
            text("UPDATE artifacts SET status = 'DELETED', deleted_at = now() WHERE id = :id"),
            {"id": artifact.id},
        )
        session.commit()
    with session_scope(worker_factory) as session, pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE artifacts SET status = 'AVAILABLE', deleted_at = NULL WHERE id = :id"),
            {"id": artifact.id},
        )


def member(
    new_client: ClientFactory, owner: TestClient, role: str, settings: Settings
) -> TestClient:
    """A second user who joined the owner's workspace with `role`, on the same storage."""
    other, email = new_client(settings), new_email()
    assert register(other, email).status_code == 201
    response = owner.post(
        f"{API}/workspaces/{my_workspace(owner)}/invitations",
        json={"email": email, "role": role},
        headers=csrf(owner),
    )
    token = response.json()["invitation_token"]
    accepted = other.post(f"{API}/invitations/accept", json={"token": token}, headers=csrf(other))
    assert accepted.status_code == 200
    return other
