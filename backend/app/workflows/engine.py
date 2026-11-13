"""The only code that changes workflow or task status (ADR-0009, ADR-0010).

Synchronous so both callers can share it: Celery workers call it with a sync `Session`; the
async API calls it through `app.services.transitions`, which runs it via
`AsyncSession.run_sync`. Callers hold the workflow row lock and own the transaction. Every
change writes its workflow event, and any dispatch it makes necessary is written to the outbox,
through the same session, so state, history and dispatch commit or roll back together.

After a task changes, `reconcile_workflow` keeps the workflow consistent with its tasks:
- required task FAILED            -> workflow FAILED (remaining tasks cancelled)
- required task ESCALATED/BLOCKED,
  or CANCELLED while still planned -> workflow BLOCKED
- every required task COMPLETED   -> workflow COMPLETED (unfinished optional tasks cancelled)
- otherwise, PENDING tasks whose dependencies all COMPLETED become READY, and PENDING tasks
  depending on a FAILED/CANCELLED task become BLOCKED.

Dispatch (outbox `task.dispatch`) is requested when a task becomes ASSIGNED in a RUNNING
workflow, for every ASSIGNED task when the workflow (re)enters RUNNING, and for waiting ASSIGNED
tasks when a running task frees an execution slot. Duplicates are harmless: the worker only acts
on a task that is still ASSIGNED.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.execution import outbox
from app.models import (
    AnalysisStatus,
    AutonomyPolicy,
    PlanStatus,
    TaskDependency,
    TaskStatus,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
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
# Statuses that occupy one of the workflow's `max_parallel_tasks` execution slots.
EXECUTION_SLOT_STATUSES = frozenset({T.QUEUED, T.IN_PROGRESS})


class InvalidTransitionError(AppError):
    status_code = 409
    code = "INVALID_TRANSITION"
    message = "This status change is not allowed."


class TransitionPreconditionError(AppError):
    status_code = 409
    code = "TRANSITION_PRECONDITION_FAILED"
    message = "The status change is not possible in the current state."


class _AddsRows(Protocol):
    def add(self, instance: object, /) -> None: ...


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
    session: _AddsRows,
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


def list_tasks(session: Session, workflow: Workflow) -> list[WorkflowTask]:
    # No populate_existing: it would overwrite unflushed changes on already-loaded tasks.
    statement = (
        select(WorkflowTask)
        .where(
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.workspace_id == workflow.workspace_id,
        )
        .order_by(WorkflowTask.created_at, WorkflowTask.id)
    )
    return list(session.execute(statement).scalars())


def _list_dependencies(
    session: Session, workflow_id: uuid.UUID
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = session.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.workflow_id == workflow_id
        )
    )
    return [(row[0], row[1]) for row in rows]


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


def request_dispatch(session: _AddsRows, workflow: Workflow, task: WorkflowTask) -> uuid.UUID:
    """Writes a `task.dispatch` outbox row in the caller's transaction (identifiers only)."""
    return outbox.enqueue(
        session,
        outbox.TASK_DISPATCH,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        payload={
            "workspace_id": str(workflow.workspace_id),
            "workflow_id": str(workflow.id),
            "task_id": str(task.id),
            "agent_version_id": str(task.assigned_agent_version_id),
            "attempt_count": task.attempt_count,
        },
    )


def request_analysis(session: Session, workflow: Workflow, *, actor: Actor) -> uuid.UUID:
    """Asks a worker to analyse the workflow's prompt (Step 2.2): writes a `workflow.analyze`
    outbox row and marks the analysis PENDING, in the caller's transaction. A newer request
    supersedes older ones; their deliveries are ignored."""
    event_id = outbox.enqueue(
        session,
        outbox.WORKFLOW_ANALYZE,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        payload={"workspace_id": str(workflow.workspace_id), "workflow_id": str(workflow.id)},
    )
    workflow.analysis_status = AnalysisStatus.PENDING
    workflow.analysis_request_id = event_id
    record_event(
        session,
        workflow,
        WorkflowEventType.ANALYSIS_REQUESTED,
        payload={"actor": actor.value, "request_id": str(event_id)},
    )
    return event_id


def request_plan(session: Session, workflow: Workflow, *, actor: Actor) -> uuid.UUID:
    """Asks a worker to draft a plan from the workflow's analysis (Step 2.3): writes a
    `workflow.plan` outbox row and marks plan generation PENDING, in the caller's transaction.
    A newer request supersedes older ones."""
    event_id = outbox.enqueue(
        session,
        outbox.WORKFLOW_PLAN,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        payload={"workspace_id": str(workflow.workspace_id), "workflow_id": str(workflow.id)},
    )
    workflow.plan_status = PlanStatus.PENDING
    workflow.plan_request_id = event_id
    record_event(
        session,
        workflow,
        WorkflowEventType.PLAN_REQUESTED,
        payload={"actor": actor.value, "request_id": str(event_id)},
    )
    return event_id


def _dispatch_waiting(session: Session, workflow: Workflow) -> None:
    if workflow.status != W.RUNNING:
        return
    for task in _active(list_tasks(session, workflow)):
        if task.status == T.ASSIGNED:
            request_dispatch(session, workflow, task)


def transition_workflow(
    session: Session,
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
    tasks = _active(list_tasks(session, workflow))
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
    if source == W.PENDING and target == W.PLANNING:
        # Planning starts with an analysis of the request, in the background.
        request_analysis(session, workflow, actor=actor)
    session.flush()

    if target in {W.CANCELLED, W.FAILED}:
        for task in tasks:
            if task.status not in TASK_TERMINAL:
                transition_task(
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
                transition_task(
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
        _dispatch_waiting(session, workflow)
        reconcile_workflow(session, workflow)


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


def transition_task(
    session: Session,
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
        _require_dependencies_completed(session, workflow, task)
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
    if target == T.ASSIGNED and workflow.status == W.RUNNING:
        request_dispatch(session, workflow, task)
    # Flush so later queries in this transaction (and DB constraints) see the change; app
    # sessions do not autoflush.
    session.flush()
    if source in EXECUTION_SLOT_STATUSES and target not in EXECUTION_SLOT_STATUSES:
        _dispatch_waiting(session, workflow)
    if reconcile:
        reconcile_workflow(session, workflow)


def _require_dependencies_completed(
    session: Session, workflow: Workflow, task: WorkflowTask
) -> None:
    edges = _list_dependencies(session, workflow.id)
    statuses = {t.id: t.status for t in list_tasks(session, workflow)}
    pending = [dep for tid, dep in edges if tid == task.id and statuses.get(dep) != T.COMPLETED]
    if pending:
        raise TransitionPreconditionError(
            "All dependencies must be completed first.",
            details={"incomplete_dependency_ids": [str(d) for d in pending]},
        )


def reconcile_workflow(session: Session, workflow: Workflow) -> None:
    """Brings the workflow status and task readiness in line with the tasks (see module doc)."""
    if workflow.status not in {W.RUNNING, W.BLOCKED}:
        return
    tasks = _active(list_tasks(session, workflow))
    required = [t for t in tasks if t.required]
    if any(t.status == T.FAILED for t in required):
        transition_workflow(
            session, workflow, W.FAILED, actor=Actor.SYSTEM, reason="a required task failed"
        )
        return
    if workflow.status == W.BLOCKED:
        return
    if required and all(t.status == T.COMPLETED for t in required):
        transition_workflow(session, workflow, W.COMPLETED, actor=Actor.SYSTEM)
        return

    edges = _list_dependencies(session, workflow.id)
    by_id = {t.id: t for t in tasks}
    for task in tasks:
        if task.status != T.PENDING:
            continue
        deps = [by_id[dep] for tid, dep in edges if tid == task.id and dep in by_id]
        if any(dep.status in {T.FAILED, T.CANCELLED} for dep in deps):
            transition_task(
                session,
                workflow,
                task,
                T.BLOCKED,
                actor=Actor.SYSTEM,
                reason="a dependency failed or was cancelled",
                reconcile=False,
            )
        elif all(dep.status == T.COMPLETED for dep in deps):
            transition_task(
                session,
                workflow,
                task,
                T.READY,
                actor=Actor.SYSTEM,
                reason="dependencies completed",
                reconcile=False,
            )

    if any(t.required and t.status in _BLOCKING_REQUIRED for t in tasks):
        transition_workflow(
            session, workflow, W.BLOCKED, actor=Actor.SYSTEM, reason="a required task is blocked"
        )


def running_count(session: Session, workflow: Workflow) -> int:
    """Tasks currently occupying an execution slot."""
    return sum(
        1 for t in _active(list_tasks(session, workflow)) if t.status in EXECUTION_SLOT_STATUSES
    )
