"""Deleting workflows (Step 9.11, ADR-0058): admins soft-delete idle workflows, which vanish from
every API read while their history stays; a worker job purges their workspace and files."""

import io
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.records import store_artifact
from app.artifacts.storage import LocalArtifactStorage
from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import (
    Artifact,
    ArtifactKind,
    ArtifactStatus,
    OutboxEvent,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
)
from app.projects import purge_job
from app.projects.workspace import ProjectWorkspaces
from tests.integration.api_helpers import API, csrf, member, my_workspace, new_email, register

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


def _owner_with_project(
    new_client: Any, prompt: str = "Build a CLI."
) -> tuple[Any, str, dict[str, Any]]:
    owner = new_client()
    assert register(owner, new_email()).status_code == 201
    base = f"{API}/workspaces/{my_workspace(owner)}/workflows"
    created = owner.post(
        base, json={"prompt": prompt, "kind": "SOFTWARE_PROJECT"}, headers=csrf(owner)
    )
    assert created.status_code == 201
    return owner, base, created.json()


def _delete(client: Any, base: str, workflow: dict[str, Any], revision: int | None = None) -> Any:
    return client.delete(
        f"{base}/{workflow['id']}",
        params={"revision": revision or workflow["revision"]},
        headers=csrf(client),
    )


def test_a_deleted_workflow_disappears_but_its_history_stays(
    new_client: Any, admin_factory: Factory
) -> None:
    owner, base, project = _owner_with_project(new_client)
    kept = owner.post(base, json={"prompt": "Write a memo."}, headers=csrf(owner)).json()

    assert _delete(owner, base, project).status_code == 204

    assert owner.get(f"{base}/{project['id']}").status_code == 404
    assert [w["id"] for w in owner.get(base).json()["items"]] == [kept["id"]]
    assert owner.get(base, params={"kind": "SOFTWARE_PROJECT"}).json()["total"] == 0
    assert _delete(owner, base, project, revision=2).status_code == 404  # already gone
    with session_scope(admin_factory) as s:
        row = s.get(Workflow, uuid.UUID(project["id"]))
        assert row is not None and row.deleted_at is not None
        assert row.deleted_by_user_id is not None and row.revision == project["revision"] + 1
        events = [
            e.event_type
            for e in s.scalars(select(WorkflowEvent).where(WorkflowEvent.workflow_id == row.id))
        ]
        assert events[0] == WorkflowEventType.WORKFLOW_CREATED
        assert events[-1] == WorkflowEventType.WORKFLOW_DELETED
        purge = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.workflow_id == row.id,
                OutboxEvent.event_type == outbox.WORKFLOW_PURGE,
            )
        ).one()
        assert purge.payload == {"workspace_id": str(row.workspace_id), "workflow_id": str(row.id)}


def test_only_admins_delete_and_only_idle_workflows(new_client: Any) -> None:
    owner, base, project = _owner_with_project(new_client)
    other = member(new_client, owner, "MEMBER")
    assert _delete(other, base, project).status_code == 403

    stale = _delete(owner, base, project, revision=project["revision"] + 5)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "WORKFLOW_REVISION_CONFLICT"

    planning = owner.post(
        f"{base}/{project['id']}/transitions",
        json={"to_status": "PLANNING", "revision": project["revision"]},
        headers=csrf(owner),
    ).json()
    active = _delete(owner, base, planning)
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "WORKFLOW_ACTIVE"
    assert active.json()["error"]["details"] == {"status": "PLANNING"}

    # Cancel, then delete: what the project page's "Cancel & delete" does.
    cancelled = owner.post(
        f"{base}/{project['id']}/transitions",
        json={"to_status": "CANCELLED", "revision": planning["revision"]},
        headers=csrf(owner),
    ).json()
    assert cancelled["status"] == "CANCELLED"
    assert _delete(owner, base, cancelled).status_code == 204


def test_the_database_refuses_deleting_a_workflow_with_work_in_flight(
    new_client: Any, admin_factory: Factory
) -> None:
    owner, base, project = _owner_with_project(new_client)
    owner.post(
        f"{base}/{project['id']}/transitions",
        json={"to_status": "PLANNING", "revision": project["revision"]},
        headers=csrf(owner),
    )
    with session_scope(admin_factory) as s, pytest.raises(IntegrityError, match="deleted_only"):
        row = s.get(Workflow, uuid.UUID(project["id"]))
        assert row is not None and row.status == WorkflowStatus.PLANNING
        row.deleted_at = row.created_at
        s.flush()


def test_the_purge_removes_the_workspace_and_stored_files(
    new_client: Any, admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    owner, base, project = _owner_with_project(new_client)
    workspace_id, workflow_id = uuid.UUID(project["workspace_id"]), uuid.UUID(project["id"])
    workspaces = ProjectWorkspaces(tmp_path / "workspaces")
    storage = LocalArtifactStorage(tmp_path / "artifacts", max_bytes=1024 * 1024)
    folder = workspaces.ensure_repository(workspace_id, workflow_id)
    with session_scope(admin_factory) as s:
        stored = store_artifact(
            s, storage, workspace_id=workspace_id, workflow_id=workflow_id,
            kind=ArtifactKind.BUILD_REPORT, filename="report.md", source=io.BytesIO(b"# Report"),
        )  # fmt: skip
        key = stored.storage_key
        s.commit()
    assert (folder / ".git").is_dir() and storage.verified_path(
        key, size_bytes=8, sha256=stored.sha256
    ).is_file()

    def purge(event: OutboxEvent) -> str:
        with session_scope(worker_factory) as s:
            return purge_job.run(
                s, workspaces, storage,
                event_id=event.id, workspace_id=workspace_id, workflow_id=workflow_id,
            )  # fmt: skip

    fake = OutboxEvent(id=uuid.uuid4())
    assert purge(fake) == "stale"  # not deleted: nothing is touched
    assert folder.is_dir()

    assert _delete(owner, base, project).status_code == 204
    with session_scope(admin_factory) as s:
        event = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.workflow_id == workflow_id,
                OutboxEvent.event_type == outbox.WORKFLOW_PURGE,
            )
        ).one()
    assert purge(event) == "purged"
    assert purge(event) == "duplicate"

    assert not folder.exists()
    assert not [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    with session_scope(admin_factory) as s:
        artifact = s.get(Artifact, stored.id)
        assert artifact is not None and artifact.status == ArtifactStatus.DELETED
        assert artifact.deleted_at is not None
