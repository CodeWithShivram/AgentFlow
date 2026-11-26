"""The integration gate of a software project (ADR-0051), run by workers.

Once every required task is COMPLETED and every completed task's accepted files are APPLIED
to the workspace, the project's stack profile checks run in the Docker sandbox (ADR-0050) on the
composed tree at the workspace's current commit. The job follows the planner's shape:

- `claim` (one transaction): consume the event, lock the workflow, check it is a RUNNING
  software project whose work is all applied and has no integration running, then record a
  RUNNING `project_integrations` row for the current commit;
- `execute` (no transaction, no database): run each check in the sandbox;
- `finish` (one transaction): store the outcome once, then, if the workflow is still RUNNING
  at that commit, complete it (PASSED) or block it with the failure code (FAILED, NOT_RUN).

A project is never COMPLETED without a PASSED integration (the engine checks it too). A sandbox
that cannot run is NOT_RUN, never passed. People can retry by resuming the blocked workflow,
or replan its work (ADR-0033).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution import outbox
from app.models import (
    AgentExecution,
    ExecutionStatus,
    IntegrationStatus,
    ProjectIntegration,
    TaskStatus,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
    WorkflowTask,
    WorkspaceApplication,
    WorkspaceApplicationStatus,
)
from app.projects.profiles import PROFILES
from app.projects.workspace import ProjectWorkspaces, WorkspaceError
from app.sandbox.runner import DockerSandbox, SandboxUnavailableError
from app.workflows import engine
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.projects")

CONSUMER = "integrate_project"
# Longer than the Celery hard time limit of the job (960 s), so only lost runs are closed.
STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class IntegrationJob:
    integration_id: uuid.UUID
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    head_sha: str
    stack_profile: str
    project_path: Path


@dataclass(frozen=True)
class IntegrationOutcome:
    status: IntegrationStatus
    checks: list[dict[str, Any]]
    error_code: str | None


def _lock(session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow | None:
    return session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    ).scalar_one_or_none()


def all_work_applied(session: Session, workflow: Workflow) -> bool:
    """Every required task is COMPLETED and every COMPLETED task's latest successful attempt is
    APPLIED to the workspace."""
    tasks = [t for t in engine.list_tasks(session, workflow) if t.removed_at is None]
    required = [t for t in tasks if t.required]
    if not required or any(t.status != TaskStatus.COMPLETED for t in required):
        return False
    for task in (t for t in tasks if t.status == TaskStatus.COMPLETED):
        if not _applied(session, task):
            return False
    return True


def _applied(session: Session, task: WorkflowTask) -> bool:
    latest = session.execute(
        select(AgentExecution.id)
        .where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == task.workflow_id,
            AgentExecution.status == ExecutionStatus.SUCCEEDED,
        )
        .order_by(AgentExecution.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return False
    status = session.execute(
        select(WorkspaceApplication.status).where(WorkspaceApplication.execution_id == latest)
    ).scalar_one_or_none()
    return status == WorkspaceApplicationStatus.APPLIED


def latest_integration(session: Session, workflow: Workflow) -> ProjectIntegration | None:
    return session.execute(
        select(ProjectIntegration)
        .where(
            ProjectIntegration.workflow_id == workflow.id,
            ProjectIntegration.workspace_id == workflow.workspace_id,
        )
        .order_by(ProjectIntegration.created_at.desc(), ProjectIntegration.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def claim(
    session: Session,
    workspaces: ProjectWorkspaces,
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> IntegrationJob | str:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return "duplicate"
    workflow = _lock(session, workspace_id, workflow_id)
    if (
        workflow is None
        or workflow.kind != WorkflowKind.SOFTWARE_PROJECT
        or workflow.status != WorkflowStatus.RUNNING
    ):
        session.commit()
        return "stale"
    if not all_work_applied(session, workflow):
        session.commit()  # the last apply requests integration again
        return "waiting"
    latest = latest_integration(session, workflow)
    if latest is not None and latest.status == IntegrationStatus.RUNNING:
        if latest.created_at > datetime.now(UTC) - STALE_AFTER:
            session.commit()
            return "running"
        # A worker lost mid-run: close the run as not run and start a new one.
        latest.status = IntegrationStatus.NOT_RUN
        latest.error_code = "WORKER_LOST"
        latest.finished_at = datetime.now(UTC)
    profile = (workflow.project_spec or {}).get("stack_profile")
    if profile not in PROFILES:
        session.commit()
        return "stale"
    try:
        project = workspaces.project_path(workflow.workspace_id, workflow.id)
        head = workspaces.head(project)
    except WorkspaceError:
        session.commit()
        return "waiting"
    integration = ProjectIntegration(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        head_sha=head,
        stack_profile=str(profile),
        status=IntegrationStatus.RUNNING,
        checks=[],
    )
    session.add(integration)
    session.commit()
    return IntegrationJob(
        integration_id=integration.id,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        head_sha=head,
        stack_profile=str(profile),
        project_path=project,
    )


def execute(sandbox: DockerSandbox, job: IntegrationJob) -> IntegrationOutcome:
    """Runs every check of the profile, outside any transaction. All checks run, so the
    evidence is complete; the gate passes only if each one did."""
    profile = PROFILES[job.stack_profile]
    checks: list[dict[str, Any]] = []
    for name, command in profile.checks:
        try:
            result = sandbox.run(profile.image, job.project_path, list(command))
        except SandboxUnavailableError as error:
            checks.append({"name": name, "outcome": "not_run"})
            return IntegrationOutcome(IntegrationStatus.NOT_RUN, checks, error.code)
        checks.append(
            {
                "name": name,
                "outcome": "passed" if result.succeeded else "failed",
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "output": result.output,
            }
        )
    if all(check["outcome"] == "passed" for check in checks):
        return IntegrationOutcome(IntegrationStatus.PASSED, checks, None)
    return IntegrationOutcome(IntegrationStatus.FAILED, checks, "CHECKS_FAILED")


def finish(
    session: Session,
    workspaces: ProjectWorkspaces,
    job: IntegrationJob,
    outcome: IntegrationOutcome,
) -> str:
    workflow = _lock(session, job.workspace_id, job.workflow_id)
    integration = session.get(ProjectIntegration, job.integration_id)
    if workflow is None or integration is None or integration.status != IntegrationStatus.RUNNING:
        session.commit()
        return "stale"
    integration.status = outcome.status
    integration.checks = outcome.checks
    integration.error_code = outcome.error_code
    integration.finished_at = datetime.now(UTC)
    session.flush()
    try:
        current = workspaces.head(job.project_path)
    except WorkspaceError:
        current = None
    if workflow.status == WorkflowStatus.RUNNING and current == job.head_sha:
        if outcome.status == IntegrationStatus.PASSED:
            engine.transition_workflow(
                session, workflow, WorkflowStatus.COMPLETED, actor=Actor.SYSTEM,
                reason="integration checks passed",
            )  # fmt: skip
            engine.request_package(session, workflow)
        else:
            engine.transition_workflow(
                session, workflow, WorkflowStatus.BLOCKED, actor=Actor.SYSTEM,
                reason=f"integration {outcome.error_code}",
            )  # fmt: skip
    session.commit()
    result = outcome.status.value.lower()
    logger.info(
        "project integration finished",
        extra={"workflow_id": str(job.workflow_id), "outcome": result},
    )
    return result
