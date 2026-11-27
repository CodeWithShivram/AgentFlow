"""Integration rework (ADR-0056): when a software project's integration checks fail, the platform
asks the tasks that own the failing files for a fix, a bounded number of times.

Completed tasks stay completed (history). For each responsible task the platform adds a **fix
task** with the same file scope, skills, agent version and criteria, plus one criterion that the
integration checks pass. Its contract carries the failed checks' output and the current content
of its scope as delimited data (`app.projects.context.fix_context`). When the fix task is
accepted and applied, its files replace that scope in the workspace and the gate runs again.

Responsibility comes from the evidence: the owners of the tracked files the failing output names
(for a fix task's scope, the newest task owning it). With no file named, every owner is asked.
After `MAX_FIX_ROUNDS` rounds a failure blocks the project for people, as before.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ProjectIntegration,
    TaskStatus,
    Workflow,
    WorkflowEventType,
    WorkflowTask,
)
from app.projects.scope import in_scope
from app.workflows import engine
from app.workflows.state_machine import Actor

MAX_FIX_ROUNDS = 2
FIX_CRITERION = {
    "id": "integration",
    "description": "With these files, the project's integration checks pass.",
}
_FIX_DESCRIPTION = (
    "The project's integration checks failed on the composed project. Fix the files in this "
    "task's file scope so the checks pass, keeping the original task's acceptance criteria. The "
    "contract includes the failed checks' output and the scope's current files."
)


def fix_rounds(session: Session, workflow: Workflow) -> int:
    """How many failed integrations already asked for fixes."""
    count = session.execute(
        select(func.count(func.distinct(WorkflowTask.fixes_integration_id))).where(
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.workspace_id == workflow.workspace_id,
            WorkflowTask.fixes_integration_id.is_not(None),
        )
    ).scalar_one()
    return int(count)


def failed_output(integration: ProjectIntegration) -> str:
    return "\n".join(
        str(check.get("output") or "")
        for check in integration.checks
        if check.get("outcome") == "failed"
    )


def responsible_tasks(
    tasks: list[WorkflowTask], output: str, tracked: list[str]
) -> list[WorkflowTask]:
    """The current owners (newest completed task per scope) of the files the output names; all
    owners when it names none. Ordered as the tasks were created."""
    owners = sorted(
        (
            t
            for t in tasks
            if t.removed_at is None and t.file_scope and t.status == TaskStatus.COMPLETED
        ),
        key=lambda t: t.created_at,
    )

    def owner(path: str) -> WorkflowTask | None:
        covering = [t for t in owners if in_scope(t.file_scope, path)]
        return covering[-1] if covering else None

    named = {id(t): t for p in tracked if p in output if (t := owner(p)) is not None}
    if named:
        chosen = list(named.values())
    else:
        latest: dict[tuple[str, ...], WorkflowTask] = {}
        for task in owners:
            latest[tuple(sorted(task.file_scope))] = task
        chosen = list(latest.values())
    return sorted(chosen, key=lambda t: t.created_at)


def schedule_fixes(
    session: Session,
    workflow: Workflow,
    integration: ProjectIntegration,
    tracked: list[str],
) -> list[WorkflowTask]:
    """Adds one fix task per responsible task and lets the scheduler dispatch them. The caller
    holds the workflow lock; the workflow stays RUNNING."""
    tasks = engine.list_tasks(session, workflow)
    targets = responsible_tasks(tasks, failed_output(integration), tracked)
    created: list[WorkflowTask] = []
    for target in targets:
        criteria: list[dict[str, Any]] = [
            c for c in target.acceptance_criteria if c.get("id") != FIX_CRITERION["id"]
        ]
        fix = WorkflowTask(
            id=uuid.uuid4(),
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            title=f"Fix integration: {target.title}"[:300],
            description=_FIX_DESCRIPTION,
            required_skills=list(target.required_skills),
            input_references=[],
            expected_output_schema=None,
            acceptance_criteria=[*criteria, dict(FIX_CRITERION)],
            deliverables=list(target.deliverables),
            priority=target.priority,
            required=True,
            status=TaskStatus.PENDING,
            revision=1,
            file_scope=list(target.file_scope),
            effort_points=target.effort_points,
            fixes_task_id=target.fixes_task_id or target.id,
            fixes_integration_id=integration.id,
            assigned_agent_id=target.assigned_agent_id,
            assigned_agent_version_id=target.assigned_agent_version_id,
        )
        session.add(fix)
        created.append(fix)
    session.flush()
    for fix in created:
        engine.record_event(
            session,
            workflow,
            WorkflowEventType.TASK_CREATED,
            task_id=fix.id,
            payload={
                "actor": Actor.SYSTEM.value,
                "reason": "integration rework",
                "fixes_task_id": str(fix.fixes_task_id),
                "integration_id": str(integration.id),
            },
        )
    engine.reconcile_workflow(session, workflow)
    return created
