"""Packaging a software project's deliverable (ADR-0052), run by workers.

`project.package` is requested when the integration gate completes a project, and by an admin
who explicitly asks for a PARTIAL package of a project that did not pass. The job consumes the
event, locks the workflow, decides the label from stored evidence (VERIFIED only when the
workspace's current commit passed the latest integration), packages the Git-tracked files of
that commit with `app.projects.packaging`, stores the archive and build report as artifacts
(ADR-0047), and records one `project_packages` row: READY, or FAILED with a fixed code.
"""

import io
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.records import discard, store_artifact
from app.artifacts.storage import ArtifactStorageError, LocalArtifactStorage, safe_filename
from app.execution import outbox
from app.models import (
    Artifact,
    ArtifactKind,
    IntegrationStatus,
    PackageLabel,
    PackageStatus,
    ProjectPackage,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
)
from app.projects import packaging
from app.projects.integration_job import latest_integration
from app.projects.profiles import PROFILES, StackProfile
from app.projects.workspace import ProjectWorkspaces, WorkspaceError
from app.workflows import engine

logger = logging.getLogger("agentflow.projects")

CONSUMER = "package_project"


def setup_commands(profile: StackProfile) -> list[str]:
    """How to set up and check the project, shown in the build report."""
    packages = " ".join(profile.dependencies)
    if profile.language == "python":
        return [
            "python -m venv .venv",
            ". .venv/bin/activate   # Windows: .venv\\Scripts\\activate",
            f"pip install {packages}",
            "PYTHONPATH=src python -m pytest --import-mode=importlib",
            '# Windows PowerShell: $env:PYTHONPATH = "src"',
        ]
    return [f"npm install {packages}", "npx tsc -p . --noEmit", "npx vitest run"]


def _task_summary(session: Session, workflow: Workflow) -> list[dict[str, Any]]:
    tasks = [t for t in engine.list_tasks(session, workflow) if t.removed_at is None]
    return [
        {
            "title": t.title,
            "status": t.status.value,
            "attempts": t.attempt_count,
            "effort_points": t.effort_points,
        }
        for t in sorted(tasks, key=lambda t: t.created_at)
    ]


def run(
    session: Session,
    workspaces: ProjectWorkspaces,
    storage: LocalArtifactStorage,
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    partial: bool = False,
    requested_by: uuid.UUID | None = None,
) -> str:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return "duplicate"
    workflow = session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    ).scalar_one_or_none()
    spec = (workflow.project_spec or {}) if workflow is not None else {}
    profile = PROFILES.get(str(spec.get("stack_profile")))
    if workflow is None or workflow.kind != WorkflowKind.SOFTWARE_PROJECT or profile is None:
        session.commit()
        return "stale"
    try:
        project = workspaces.project_path(workflow.workspace_id, workflow.id)
        head = workspaces.head(project)
        tracked = workspaces.tracked_files(project)
    except WorkspaceError:
        session.commit()
        return "stale"
    integration = latest_integration(session, workflow)
    verified = (
        integration is not None
        and integration.status == IntegrationStatus.PASSED
        and integration.head_sha == head
    )
    if not verified and not partial:
        session.commit()  # only verified projects are packaged automatically
        return "stale"
    if verified and workflow.status != WorkflowStatus.COMPLETED and not partial:
        session.commit()
        return "stale"
    label = PackageLabel.VERIFIED if verified else PackageLabel.PARTIAL
    done = session.execute(
        select(ProjectPackage.id).where(
            ProjectPackage.workflow_id == workflow.id,
            ProjectPackage.workspace_id == workflow.workspace_id,
            ProjectPackage.head_sha == head,
            ProjectPackage.label == label,
            ProjectPackage.status == PackageStatus.READY,
        )
    ).scalar_one_or_none()
    if done is not None:
        session.commit()
        return "duplicate"

    name = safe_filename(str(spec.get("name") or "project"), default="project")
    record = ProjectPackage(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        integration_id=integration.id if verified and integration is not None else None,
        head_sha=head,
        label=label,
        requested_by_user_id=requested_by if partial else None,
    )
    checks = [
        {"name": c.get("name"), "outcome": c.get("outcome")}
        for c in (integration.checks if integration is not None else [])
    ]
    stored: list[Artifact] = []
    savepoint = session.begin_nested()  # artifact rows roll back with their bytes
    try:
        with tempfile.TemporaryDirectory(prefix="agentflow-pkg-") as scratch:
            target = Path(scratch) / "package.zip"
            report_text: dict[str, str] = {}

            def report(entries: list[packaging.ManifestEntry], excluded: int) -> str:
                report_text["text"] = packaging.build_report(
                    project=spec,
                    label=label.value,
                    head_sha=head,
                    tasks=_task_summary(session, workflow),
                    checks=checks,
                    setup=setup_commands(profile),
                    entries=entries,
                    excluded=excluded,
                )
                return report_text["text"]

            entries, excluded = packaging.package(
                project, tracked, target, folder=name, report=report
            )
            suffix = "" if label == PackageLabel.VERIFIED else "-partial"
            with target.open("rb") as archive:
                stored.append(
                    store_artifact(
                        session, storage,
                        workspace_id=workflow.workspace_id, workflow_id=workflow.id,
                        kind=ArtifactKind.PROJECT_ARCHIVE,
                        filename=f"{name}-{head[:8]}{suffix}.zip", source=archive,
                    )
                )  # fmt: skip
            stored.append(
                store_artifact(
                    session, storage,
                    workspace_id=workflow.workspace_id, workflow_id=workflow.id,
                    kind=ArtifactKind.BUILD_REPORT,
                    filename=f"{name}-{head[:8]}{suffix}-build-report.md",
                    source=io.BytesIO(report_text["text"].encode()),
                )
            )  # fmt: skip
        record.status = PackageStatus.READY
        record.archive_artifact_id, record.report_artifact_id = stored[0].id, stored[1].id
        record.file_count, record.excluded_count = len(entries), excluded
        savepoint.commit()
    except (packaging.PackagingError, ArtifactStorageError) as error:
        savepoint.rollback()
        for artifact in stored:
            discard(storage, artifact)
        stored = []
        record.status, record.error_code = PackageStatus.FAILED, error.code
    try:
        session.add(record)
        session.commit()
    except BaseException:
        for artifact in stored:
            discard(storage, artifact)
        raise
    outcome = record.status.value.lower()
    logger.info(
        "project packaging finished",
        extra={"workflow_id": str(workflow.id), "outcome": outcome, "label": label.value},
    )
    return outcome
