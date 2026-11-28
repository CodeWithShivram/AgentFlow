"""Context for software project tasks (ADR-0049): the accepted files of a task's dependencies,
so dependent work (tests, integration) fits what was built before it.

Files come from each dependency's latest successful attempt, which platform verification
accepted (the dependency is COMPLETED before this task is dispatched). They are agent output:
they reach models only inside the delimited contract, as data. Bounded by file count and total
characters, in dependency order and then path order; what does not fit is left out.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentExecution, ExecutionStatus, ProjectIntegration, Workflow, WorkflowTask
from app.projects.apply_job import accepted_files
from app.projects.workspace import ProjectWorkspaces, WorkspaceError

MAX_DEPENDENCY_FILES = 40
MAX_DEPENDENCY_CHARS = 120_000


def dependency_files(
    session: Session, workflow: Workflow, dependencies: list[uuid.UUID]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for task_id in dependencies:
        execution = session.execute(
            select(AgentExecution)
            .where(
                AgentExecution.task_id == task_id,
                AgentExecution.workflow_id == workflow.id,
                AgentExecution.workspace_id == workflow.workspace_id,
                AgentExecution.status == ExecutionStatus.SUCCEEDED,
            )
            .order_by(AgentExecution.attempt_number.desc())
            .limit(1)
        ).scalar_one_or_none()
        if execution is None:
            continue
        try:
            owner = session.get(WorkflowTask, task_id)
            files = accepted_files(execution.submission, list(owner.file_scope) if owner else None)
        except WorkspaceError:
            continue
        for path in sorted(files):
            content = files[path]
            if len(selected) >= MAX_DEPENDENCY_FILES or used + len(content) > MAX_DEPENDENCY_CHARS:
                return selected
            selected.append({"path": path, "content": content})
            used += len(content)
    return selected


MAX_FEEDBACK_CHARS = 12_000
MAX_CURRENT_FILES = 40
MAX_CURRENT_CHARS = 120_000


def fix_context(
    session: Session, workflow: Workflow, task: WorkflowTask, workspaces: ProjectWorkspaces
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """For an integration fix task (ADR-0056), and any task of a change project (ADR-0059, no
    feedback): the failed checks' output (already capped and
    redacted by the sandbox, capped again here), the current files of its scope, and the rest of
    the project's files as read-only context (a tests fix must see how the code it tests works)."""
    if task.fixes_integration_id is None and workflow.base_workflow_id is None:
        return [], [], []
    integration = (
        session.get(ProjectIntegration, task.fixes_integration_id)
        if task.fixes_integration_id is not None
        else None
    )
    feedback: list[dict[str, Any]] = []
    used = 0
    for check in integration.checks if integration is not None else []:
        if check.get("outcome") != "failed":
            continue
        output = str(check.get("output") or "")[-max(MAX_FEEDBACK_CHARS - used, 0) :]
        used += len(output)
        feedback.append({"check": str(check.get("name")), "output": output})
    try:
        project = workspaces.project_path(workflow.workspace_id, workflow.id)
        files = workspaces.read_scope(
            project,
            list(task.file_scope),
            max_files=MAX_CURRENT_FILES,
            max_chars=MAX_CURRENT_CHARS,
        )
        everything = workspaces.read_scope(
            project, None, max_files=MAX_DEPENDENCY_FILES * 2, max_chars=MAX_DEPENDENCY_CHARS * 2
        )
    except WorkspaceError:
        files, everything = {}, {}
    others = {p: c for p, c in everything.items() if p not in files}
    selected: list[dict[str, Any]] = []
    used = 0
    for path in sorted(others):
        content = others[path]
        if len(selected) >= MAX_DEPENDENCY_FILES or used + len(content) > MAX_DEPENDENCY_CHARS:
            break
        selected.append({"path": path, "content": content})
        used += len(content)
    current = [{"path": p, "content": c} for p, c in files.items()]
    return feedback, current, selected
