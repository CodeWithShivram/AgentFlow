"""Applying an accepted task to its project workspace (ADR-0049), run by workers.

The engine writes `project.apply` in the transaction that moves a software project's task to
COMPLETED, which only platform verification can do (ADR-0034). The job consumes the event (a
duplicate stops here), locks the workflow (so one project's applies never run concurrently),
takes the task's latest successful attempt, and makes the task's `file_scope` in the workspace
match that attempt's accepted files. The outcome, APPLIED with its commit or FAILED with a fixed
code, is stored once per attempt in `workspace_applications`, together with the inbox row.
"""

import logging
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.outputs import DeveloperOutputs
from app.execution import outbox
from app.models import (
    AgentExecution,
    ExecutionStatus,
    TaskStatus,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
    WorkflowTask,
    WorkspaceApplication,
    WorkspaceApplicationStatus,
)
from app.projects import changes
from app.projects.integration_job import all_work_applied
from app.projects.scope import in_scope
from app.projects.workspace import ProjectWorkspaces, WorkspaceError
from app.workflows import engine

logger = logging.getLogger("agentflow.projects")

CONSUMER = "apply_to_workspace"


def accepted_files(
    submission: dict[str, Any] | None, scope: list[str] | None = None
) -> dict[str, str]:
    """The files of an accepted Developer submission (proposed files and tests), by path. With a
    scope, extra tests outside it are left out: another task owns them (verification v3 only
    warns about them). Raises WorkspaceError("OUTPUTS_INVALID") when the outputs do not parse."""
    try:
        outputs = DeveloperOutputs.model_validate((submission or {}).get("outputs") or {})
    except ValidationError:
        raise WorkspaceError("OUTPUTS_INVALID") from None
    tests = [t for t in outputs.proposed_tests if scope is None or in_scope(scope, t.path)]
    return {f.path: f.content for f in (*outputs.proposed_files, *tests)}


def _latest_success(session: Session, task: WorkflowTask) -> AgentExecution | None:
    return session.execute(
        select(AgentExecution)
        .where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == task.workflow_id,
            AgentExecution.workspace_id == task.workspace_id,
            AgentExecution.status == ExecutionStatus.SUCCEEDED,
        )
        .order_by(AgentExecution.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def run(
    session: Session,
    workspaces: ProjectWorkspaces,
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
) -> str:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return "duplicate"
    workflow = session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    ).scalar_one_or_none()
    task = (
        session.execute(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.workflow_id == workflow.id,
                WorkflowTask.workspace_id == workflow.workspace_id,
            )
        ).scalar_one_or_none()
        if workflow is not None
        else None
    )
    execution = _latest_success(session, task) if task is not None else None
    if (
        workflow is None
        or workflow.kind != WorkflowKind.SOFTWARE_PROJECT
        or task is None
        or task.status != TaskStatus.COMPLETED
        or not task.file_scope
        or execution is None
    ):
        session.commit()  # consumed: nothing to apply
        return "stale"
    done = session.execute(
        select(WorkspaceApplication.id).where(
            WorkspaceApplication.execution_id == execution.id,
            WorkspaceApplication.workspace_id == workflow.workspace_id,
        )
    ).scalar_one_or_none()
    if done is not None:
        session.commit()
        return "duplicate"

    application = WorkspaceApplication(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        execution_id=execution.id,
    )
    try:
        files = accepted_files(execution.submission, list(task.file_scope))
        changes.seed_change_workspace(workspaces, workflow)  # ADR-0059; idempotent
        change = workspaces.apply(
            workflow.workspace_id,
            workflow.id,
            scope=list(task.file_scope),
            files=files,
            message=f"{task.title[:150]} (attempt {execution.attempt_number})",
        )
    except WorkspaceError as error:
        application.status = WorkspaceApplicationStatus.FAILED
        application.error_code = error.code
    else:
        application.status = WorkspaceApplicationStatus.APPLIED
        application.commit_sha = change.commit_sha
        application.head_sha = change.head_sha
        application.files_written = change.files_written
        application.files_removed = change.files_removed
        application.tree_sha256 = change.tree_sha256
    session.add(application)
    session.flush()
    # The engine requests integration when the last task completes, which can be before its
    # files are applied; the last apply asks again (ADR-0051).
    if (
        application.status == WorkspaceApplicationStatus.APPLIED
        and workflow.status == WorkflowStatus.RUNNING
        and all_work_applied(session, workflow)
    ):
        engine.request_integration(session, workflow)
    session.commit()
    outcome = application.status.value.lower()
    logger.info(
        "workspace apply finished",
        extra={"workflow_id": str(workflow.id), "task_id": str(task.id), "outcome": outcome},
    )
    return outcome
