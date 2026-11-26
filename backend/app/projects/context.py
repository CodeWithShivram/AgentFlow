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

from app.models import AgentExecution, ExecutionStatus, Workflow, WorkflowTask
from app.projects.apply_job import accepted_files
from app.projects.workspace import WorkspaceError

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
