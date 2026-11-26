"""Deliverable packaging (Step 9.6, ADR-0052): verified projects are packaged automatically;
admins may ask for an explicitly PARTIAL package; secrets refuse a package; downloads stay
workspace-scoped."""

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.storage import LocalArtifactStorage
from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import (
    Artifact,
    ArtifactKind,
    PackageLabel,
    PackageStatus,
    ProjectPackage,
    WorkflowStatus,
)
from app.projects import package_job
from app.projects.workspace import ProjectWorkspaces
from tests.integration.api_helpers import API, csrf, member, my_workspace, new_email, register
from tests.integration.test_project_integration import (
    _built_and_applied,
    _FakeSandbox,
    _integrate,
    _workflow,
)
from tests.integration.test_project_workspace import _events

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


@pytest.fixture
def workspaces(tmp_path: Path) -> ProjectWorkspaces:
    return ProjectWorkspaces(tmp_path / "workspaces")


@pytest.fixture
def storage(tmp_path: Path) -> LocalArtifactStorage:
    return LocalArtifactStorage(tmp_path / "artifacts", max_bytes=10 * 1024 * 1024)


def _package(
    worker: Factory,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
    event: Any,
) -> str:
    with session_scope(worker) as s:
        return package_job.run(
            s,
            workspaces,
            storage,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
            partial=bool(event.payload.get("partial")),
            requested_by=(
                uuid.UUID(event.payload["requested_by"])
                if event.payload.get("requested_by")
                else None
            ),
        )


def _packages(admin: Factory) -> list[ProjectPackage]:
    with session_scope(admin) as s:
        return list(s.scalars(select(ProjectPackage).order_by(ProjectPackage.created_at)))


def _artifact(admin: Factory, artifact_id: uuid.UUID | None) -> Artifact:
    with session_scope(admin) as s:
        artifact = s.get(Artifact, artifact_id)
        assert artifact is not None
        return artifact


def _archive(storage: LocalArtifactStorage, artifact: Artifact) -> zipfile.ZipFile:
    path = storage.verified_path(
        artifact.storage_key, size_bytes=artifact.size_bytes, sha256=artifact.sha256
    )
    return zipfile.ZipFile(io.BytesIO(path.read_bytes()))


def test_a_verified_project_is_packaged_automatically(
    admin_factory: Factory,
    worker_factory: Factory,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
) -> None:
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    integrate = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), integrate) == "passed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED

    (event,) = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)
    assert _package(worker_factory, workspaces, storage, event) == "ready"
    assert _package(worker_factory, workspaces, storage, event) == "duplicate"

    (package,) = _packages(admin_factory)
    assert (package.status, package.label) == (PackageStatus.READY, PackageLabel.VERIFIED)
    assert package.integration_id is not None and package.file_count == 4
    archive_row = _artifact(admin_factory, package.archive_artifact_id)
    assert archive_row.kind == ArtifactKind.PROJECT_ARCHIVE
    assert archive_row.filename == f"tool-{package.head_sha[:8]}.zip"
    with _archive(storage, archive_row) as archive:
        names = set(archive.namelist())
        report = archive.read("tool/AGENTFLOW_BUILD_REPORT.md").decode()
        manifest = json.loads(archive.read("tool/AGENTFLOW_MANIFEST.json"))
    assert {"tool/pyproject.toml", "tool/README.md"} <= names
    assert "tool/src/mock_module.py" in names and "tool/tests/test_mock.py" in names
    assert not any("/.git/" in n for n in names)
    assert "Status: **VERIFIED**" in report and "pip install" in report
    assert len(manifest["files"]) == 4
    report_row = _artifact(admin_factory, package.report_artifact_id)
    assert report_row.kind == ArtifactKind.BUILD_REPORT


def test_unverified_projects_are_never_packaged_automatically(
    admin_factory: Factory,
    worker_factory: Factory,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
) -> None:
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    integrate = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert (
        _integrate(worker_factory, workspaces, _FakeSandbox(succeed=False), integrate) == "failed"
    )
    assert _events(admin_factory, graph, outbox.PROJECT_PACKAGE) == []
    # Even a forged automatic request changes nothing.
    with session_scope(admin_factory) as s:
        from app.models import Workflow
        from app.workflows import engine

        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        engine.request_package(s, workflow)
        s.commit()
    (forged,) = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)
    assert _package(worker_factory, workspaces, storage, forged) == "stale"
    assert _packages(admin_factory) == []


def test_an_admin_can_ask_for_an_explicitly_partial_package(
    admin_factory: Factory,
    worker_factory: Factory,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
) -> None:
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    integrate = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    _integrate(worker_factory, workspaces, _FakeSandbox(succeed=False), integrate)
    with session_scope(admin_factory) as s:
        from app.models import Workflow
        from app.workflows import engine

        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        engine.request_package(s, workflow, partial_by=workflow.created_by_user_id)
        s.commit()
    (event,) = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)
    assert _package(worker_factory, workspaces, storage, event) == "ready"

    (package,) = _packages(admin_factory)
    assert package.label == PackageLabel.PARTIAL and package.integration_id is None
    assert package.requested_by_user_id is not None
    archive_row = _artifact(admin_factory, package.archive_artifact_id)
    assert archive_row.filename.endswith("-partial.zip")
    with _archive(storage, archive_row) as archive:
        report = archive.read("tool/AGENTFLOW_BUILD_REPORT.md").decode()
    assert "Status: **PARTIAL**" in report and "- `compile`: failed" in report


def test_a_committed_secret_refuses_the_package_and_stores_nothing(
    admin_factory: Factory,
    worker_factory: Factory,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
) -> None:
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    workspaces.apply(
        graph.workspace_id,
        graph.workflow_id,
        scope=["src/"],
        files={"src/settings.py": "AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\n"},
        message="leak",
    )
    with session_scope(admin_factory) as s:
        from app.models import Workflow
        from app.workflows import engine

        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        engine.request_package(s, workflow, partial_by=workflow.created_by_user_id)
        s.commit()
    (event,) = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)
    assert _package(worker_factory, workspaces, storage, event) == "failed"
    (package,) = _packages(admin_factory)
    assert (package.status, package.error_code) == (PackageStatus.FAILED, "SECRET_DETECTED")
    assert package.archive_artifact_id is None
    with session_scope(admin_factory) as s:
        assert list(s.scalars(select(Artifact))) == []
    assert not [p for p in (storage._configured_root).rglob("*") if p.is_file()]


def test_package_api_is_admin_only_and_for_software_projects(new_client: Any) -> None:
    owner = new_client()
    assert register(owner, new_email()).status_code == 201
    base = f"{API}/workspaces/{my_workspace(owner)}/workflows"
    general = owner.post(base, json={"prompt": "Write a memo."}, headers=csrf(owner)).json()
    project = owner.post(
        base, json={"prompt": "Build a CLI.", "kind": "SOFTWARE_PROJECT"}, headers=csrf(owner)
    ).json()

    assert owner.get(f"{base}/{project['id']}/packages").json() == []
    refused = owner.post(f"{base}/{general['id']}/packages", headers=csrf(owner))
    assert (
        refused.status_code == 409 and refused.json()["error"]["code"] == "NOT_A_SOFTWARE_PROJECT"
    )
    early = owner.post(f"{base}/{project['id']}/packages", headers=csrf(owner))
    assert early.status_code == 409 and early.json()["error"]["code"] == "PACKAGING_NOT_AVAILABLE"
    other = member(new_client, owner, "MEMBER")
    assert other.post(f"{base}/{project['id']}/packages", headers=csrf(other)).status_code == 403
