"""The only code that changes workflow or task status (ADR-0009).

Callers must hold the workflow row lock (repositories.workflows.lock_workflow) and own the
transaction; every change writes its workflow event through the same session, so state and
history commit or roll back together.

After a task changes, `reconcile_workflow` keeps the workflow consistent with its tasks:
- required task FAILED            -> workflow FAILED (remaining tasks cancelled)
- required task ESCALATED/BLOCKED,
  or CANCELLED while still planned -> workflow BLOCKED
- every required task COMPLETED   -> workflow COMPLETED (unfinished optional tasks cancelled)
- otherwise, PENDING tasks whose dependencies all COMPLETED become READY, and PENDING tasks
  depending on a FAILED/CANCELLED task become BLOCKED.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    AutonomyPolicy,
    TaskStatus,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
from app.repositories import workflows as repository
from app.workflows.state_machine import (
    TASK_STATUSES_NEEDING_RUNNING_WORKFLOW,
    TASK_TERMINAL,
    WORKFLOW_EXECUTING,
    WORKFLOW_TERMINAL,
    Actor,
    task_transition_allowed,
    workflow_transition_allowed,
)

W, T = WorkflowStatus, TaskStatus
_BLOCKING_REQUIRED = frozenset({T.ESCALATED, T.BLOCKED, T.CANCELLED})


class InvalidTransitionError(AppError):
    status_code = 409
    code = "INVALID_TRANSITION"
    message = "This status change is not allowed."


class TransitionPreconditionError(AppError):
    status_code = 409
    code = "TRANSITION_PRECONDITION_FAILED"
    message = "The status change is not possible in the current state."


_WORKFLOW_EVENTS = {
    W.COMPLETED: WorkflowEventType.WORKFLOW_COMPLETED,
    W.FAILED: WorkflowEventType.WORKFLOW_FAILED,
    W.CANCELLED: WorkflowEventType.WORKFLOW_CANCELLED,
    W.AWAITING_APPROVAL: WorkflowEventType.APPROVAL_REQUESTED,
}
_TASK_EVENTS = {
    T.ASSIGNED: WorkflowEventType.TASK_ASSIGNED,
    T.QUEUED: WorkflowEventType.TASK_QUEUED,
    T.IN_PROGRESS: WorkflowEventType.TASK_STARTED,
    T.SUBMITTED: WorkflowEventType.AGENT_SUBMITTED,
    T.VERIFYING: WorkflowEventType.VERIFICATION_STARTED,
    T.COMPLETED: WorkflowEventType.TASK_COMPLETED,
    T.WAITING_APPROVAL: WorkflowEventType.APPROVAL_REQUESTED,
}


def record_event(
    session: AsyncSession,
    workflow: Workflow,
    event_type: WorkflowEventType,
    *,
    task_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Appends a workflow event in the caller's transaction. Never put prompts or secrets in
    the payload."""
    session.add(
        WorkflowEvent(
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            task_id=task_id,
            event_type=event_type,
            payload=payload or {},
        )
    )


def _workflow_event_type(source: WorkflowStatus, target: WorkflowStatus) -> WorkflowEventType:
    if source == W.AWAITING_APPROVAL and target == W.READY:
        return WorkflowEventType.APPROVAL_RESOLVED
    if source == W.PLANNING and target == W.READY:
        return WorkflowEventType.PLAN_CREATED
    return _WORKFLOW_EVENTS.get(target, WorkflowEventType.WORKFLOW_STATUS_CHANGED)


def _task_event_type(source: TaskStatus, target: TaskStatus) -> WorkflowEventType:
    if source == T.VERIFYING and target == T.REWORK_REQUIRED:
        return WorkflowEventType.VERIFICATION_FAILED
    return _TASK_EVENTS.get(target, WorkflowEventType.TASK_STATUS_CHANGED)


def _active(tasks: list[WorkflowTask]) -> list[WorkflowTask]:
    return [task for task in tasks if task.removed_at is None]


async def transition_workflow(
    session: AsyncSession,
    workflow: Workflow,
    target: WorkflowStatus,
    *,
    actor: Actor,
    reason: str | None = None,
) -> None:
    source = workflow.status
    if not workflow_transition_allowed(source, target, actor):
        raise InvalidTransitionError(
            details={"from": source.value, "to": target.value, "actor": actor.value}
        )
    tasks = _active(await repository.list_tasks(session, workflow))
    _check_workflow_guards(workflow, source, target, tasks)

    now = datetime.now(UTC)
    workflow.status = target
    workflow.revision += 1
    if target == W.RUNNING and workflow.started_at is None:
        workflow.started_at = now
    if target in WORKFLOW_TERMINAL:
        workflow.completed_at = now
    payload: dict[str, Any] = {"from": source.value, "to": target.value, "actor": actor.value}
    if reason:
        payload["reason"] = reason
    record_event(session, workflow, _workflow_event_type(source, target), payload=payload)
    await session.flush()

    if target in {W.CANCELLED, W.FAILED}:
        for task in tasks:
            if task.status not in TASK_TERMINAL:
                await transition_task(
                    session,
                    workflow,
                    task,
                    T.CANCELLED,
                    actor=Actor.SYSTEM,
                    reason=f"workflow {target.value.lower()}",
                    closing_workflow=True,
                    reconcile=False,
                )
    elif target == W.COMPLETED:
        for task in tasks:
            if not task.required and task.status not in TASK_TERMINAL:
                await transition_task(
                    session,
                    workflow,
                    task,
                    T.CANCELLED,
                    actor=Actor.SYSTEM,
                    reason="workflow completed",
                    closing_workflow=True,
                    reconcile=False,
                )
    elif target == W.RUNNING:
        await reconcile_workflow(session, workflow)


def _check_workflow_guards(
    workflow: Workflow,
    source: WorkflowStatus,
    target: WorkflowStatus,
    tasks: list[WorkflowTask],
) -> None:
    if source == W.PLANNING and target in {W.AWAITING_APPROVAL, W.READY}:
        if not tasks:
            raise TransitionPreconditionError("A plan needs at least one task.")
        if target == W.READY and workflow.autonomy_policy != AutonomyPolicy.AUTONOMOUS:
            raise TransitionPreconditionError(
                "This workflow requires plan approval; submit it for approval instead."
            )
    if target == W.COMPLETED:
        unresolved = [t for t in tasks if t.required and t.status != T.COMPLETED]
        if unresolved:
            raise TransitionPreconditionError(
                "Required tasks are not completed.",
                details={"unresolved_task_ids": [str(t.id) for t in unresolved]},
            )
    if source == W.BLOCKED and target == W.RUNNING:
        blocking = [t for t in tasks if t.required and t.status in _BLOCKING_REQUIRED]
        if blocking:
            raise TransitionPreconditionError(
                "Resolve the blocking tasks first.",
                details={"blocking_task_ids": [str(t.id) for t in blocking]},
            )


async def transition_task(
    session: AsyncSession,
    workflow: Workflow,
    task: WorkflowTask,
    target: TaskStatus,
    *,
    actor: Actor,
    reason: str | None = None,
    event_type: WorkflowEventType | None = None,
    extra: dict[str, Any] | None = None,
    reconcile: bool = True,
    closing_workflow: bool = False,
) -> None:
    """`closing_workflow` is set only by transition_workflow when it cancels the leftover tasks
    of a workflow it has just moved to a terminal status."""
    source = task.status
    if not task_transition_allowed(source, target, actor):
        raise InvalidTransitionError(
            details={"from": source.value, "to": target.value, "actor": actor.value}
        )
    if workflow.status in WORKFLOW_TERMINAL and not (closing_workflow and target == T.CANCELLED):
        raise TransitionPreconditionError("The workflow has finished.")
    if target != T.CANCELLED:
        if workflow.status not in WORKFLOW_EXECUTING:
            raise TransitionPreconditionError("The workflow is not running.")
        if target in TASK_STATUSES_NEEDING_RUNNING_WORKFLOW and workflow.status != W.RUNNING:
            raise TransitionPreconditionError(
                "Tasks cannot be scheduled while the workflow is paused or blocked."
            )
    if target == T.READY:
        await _require_dependencies_completed(session, workflow, task)
    if target == T.ASSIGNED and task.assigned_agent_version_id is None:
        raise TransitionPreconditionError("Assign an agent version first.")

    now = datetime.now(UTC)
    task.status = target
    task.revision += 1
    if target == T.IN_PROGRESS and task.started_at is None:
        task.started_at = now
    if target in TASK_TERMINAL:
        task.completed_at = now
    payload: dict[str, Any] = {"from": source.value, "to": target.value, "actor": actor.value}
    if reason:
        payload["reason"] = reason
    payload.update(extra or {})
    record_event(
        session,
        workflow,
        event_type or _task_event_type(source, target),
        task_id=task.id,
        payload=payload,
    )
    # Flush so later queries in this transaction (and DB constraints) see the change; app
    # sessions do not autoflush.
    await session.flush()
    if reconcile:
        await reconcile_workflow(session, workflow)


async def _require_dependencies_completed(
    session: AsyncSession, workflow: Workflow, task: WorkflowTask
) -> None:
    edges = await repository.list_dependencies(session, workflow.id)
    statuses = {t.id: t.status for t in await repository.list_tasks(session, workflow)}
    pending = [dep for tid, dep in edges if tid == task.id and statuses.get(dep) != T.COMPLETED]
    if pending:
        raise TransitionPreconditionError(
            "All dependencies must be completed first.",
            details={"incomplete_dependency_ids": [str(d) for d in pending]},
        )


async def reconcile_workflow(session: AsyncSession, workflow: Workflow) -> None:
    """Brings the workflow status and task readiness in line with the tasks (see module doc)."""
    if workflow.status not in {W.RUNNING, W.BLOCKED}:
        return
    tasks = _active(await repository.list_tasks(session, workflow))
    required = [t for t in tasks if t.required]
    if any(t.status == T.FAILED for t in required):
        await transition_workflow(
            session, workflow, W.FAILED, actor=Actor.SYSTEM, reason="a required task failed"
        )
        return
    if workflow.status == W.BLOCKED:
        return
    if required and all(t.status == T.COMPLETED for t in required):
        await transition_workflow(session, workflow, W.COMPLETED, actor=Actor.SYSTEM)
        return

    edges = await repository.list_dependencies(session, workflow.id)
    by_id = {t.id: t for t in tasks}
    for task in tasks:
        if task.status != T.PENDING:
            continue
        deps = [by_id[dep] for tid, dep in edges if tid == task.id and dep in by_id]
        if any(dep.status in {T.FAILED, T.CANCELLED} for dep in deps):
            await transition_task(
                session,
                workflow,
                task,
                T.BLOCKED,
                actor=Actor.SYSTEM,
                reason="a dependency failed or was cancelled",
                reconcile=False,
            )
        elif all(dep.status == T.COMPLETED for dep in deps):
            await transition_task(
                session,
                workflow,
                task,
                T.READY,
                actor=Actor.SYSTEM,
                reason="dependencies completed",
                reconcile=False,
            )

    if any(t.required and t.status in _BLOCKING_REQUIRED for t in tasks):
        await transition_workflow(
            session, workflow, W.BLOCKED, actor=Actor.SYSTEM, reason="a required task is blocked"
        )
