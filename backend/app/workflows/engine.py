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

A task reaching SUBMITTED requests its verification (outbox `task.verify`, ADR-0030).

Dispatch (outbox `task.dispatch`) is requested when a task becomes ASSIGNED in a RUNNING
workflow, for every ASSIGNED task when the workflow (re)enters RUNNING, and for waiting ASSIGNED
tasks when a running task frees an execution slot. Duplicates are harmless: the worker only acts
on a task that is still ASSIGNED.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.execution import outbox
from app.llm import costing
from app.models import (
    AgentExecution,
    AnalysisStatus,
    ApprovalDecision,
    AutonomyPolicy,
    ExecutionStatus,
    HumanIntervention,
    InterventionStatus,
    InterventionSubject,
    InterventionType,
    PauseReason,
    PlanStatus,
    TaskDependency,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
    WorkspaceRole,
)
from app.workflows.state_machine import (
    MAX_ATTEMPTS_PER_TASK,
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


# Reasons recorded when an agent's request is rejected and stopped, or expires (ADR-0037).
APPROVAL_REJECTED = "APPROVAL_REJECTED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
# Recorded when spend reaches the workflow's budget (ADR-0040).
BUDGET_REACHED = "BUDGET_REACHED"
# Reasons recorded with the platform's own pause and resume (ADR-0036).
AWAITING_DECISION = "AWAITING_DECISION"
DECISIONS_MADE = "DECISIONS_MADE"

# --- human interventions (ADR-0035) ---------------------------------------------------------
# Requests that need a person. Opened and closed in the transaction of the change that causes
# them, with an event; events carry identifiers only, never the request's text.

_ESCALATION_ACTION = (
    "Decide how this task continues: reassign it (a new attempt), cancel it, or replan the "
    "remaining work."
)
_PLAN_APPROVAL_ACTION = "Approve the plan so the workflow can start, or send it back to planning."
_CONTEXT_KEYS = ("error_code", "failure_codes", "issue_codes", "attempt_number", "verifier")


def action_fingerprint(row: HumanIntervention) -> str:
    """SHA-256 of exactly what an approval request showed (ADR-0038): its identity, subject,
    requested action and context. Stored with the decision; checked again before an approved
    action reaches an agent, so an approval binds to that action and nothing else."""
    shown = {
        "intervention_id": str(row.id),
        "workflow_id": str(row.workflow_id),
        "task_id": str(row.task_id) if row.task_id else None,
        "subject": row.subject,
        "requested_action": row.requested_action,
        "context": row.context,
    }
    encoded = json.dumps(shown, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_intervention(
    session: Session,
    workflow: Workflow,
    *,
    type: InterventionType,
    subject: InterventionSubject,
    requested_action: str,
    context: dict[str, Any],
    source_key: str,
    task_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    required_role: WorkspaceRole | None = None,
    requested_by: str = "SYSTEM",
    requested_by_agent_id: uuid.UUID | None = None,
) -> HumanIntervention | None:
    """Opens a request once per source; returns None when it already exists. An APPROVAL
    names the least role that may decide it (ADR-0038)."""
    if (type == InterventionType.APPROVAL) != (required_role is not None):
        raise ValueError("an approval request, and only one, needs a required_role")
    exists = session.execute(
        select(HumanIntervention.id).where(
            HumanIntervention.workflow_id == workflow.id,
            HumanIntervention.workspace_id == workflow.workspace_id,
            HumanIntervention.source_key == source_key,
        )
    ).first()
    if exists is not None:
        return None
    row = HumanIntervention(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task_id,
        type=type,
        subject=subject.value,
        status=InterventionStatus.OPEN,
        requested_action=requested_action,
        context=context,
        expires_at=expires_at,
        required_role=required_role.value if required_role else None,
        requested_by=requested_by,
        requested_by_agent_id=requested_by_agent_id,
        source_key=source_key,
    )
    session.add(row)
    record_event(
        session,
        workflow,
        WorkflowEventType.INTERVENTION_OPENED,
        task_id=task_id,
        payload={
            "intervention_id": str(row.id),
            "type": type.value,
            "subject": subject.value,
            "requested_by": requested_by,
        },
    )
    return row


def resolve_intervention(
    session: Session,
    workflow: Workflow,
    row: HumanIntervention,
    *,
    status: InterventionStatus,
    resolution: dict[str, Any],
    resolved_by_user_id: uuid.UUID | None = None,
    decided_role: WorkspaceRole | None = None,
) -> None:
    """Closes one open request. `resolution` holds a decision, an answer or an outcome. A
    decision on an APPROVAL also writes its immutable audit record (ADR-0038), with the
    decider's role at that moment."""
    if row.type == InterventionType.APPROVAL and "decision" in resolution:
        execution_id = row.context.get("execution_id")
        session.add(
            ApprovalDecision(
                id=uuid.uuid4(),
                workspace_id=row.workspace_id,
                workflow_id=row.workflow_id,
                intervention_id=row.id,
                task_id=row.task_id,
                execution_id=uuid.UUID(execution_id) if execution_id else None,
                subject=row.subject,
                decision=resolution["decision"],
                then=resolution.get("then"),
                note=resolution.get("note"),
                required_role=row.required_role,
                decided_by_user_id=resolved_by_user_id,
                decided_role=decided_role.value if decided_role else None,
                expired=status == InterventionStatus.EXPIRED,
                action_fingerprint=action_fingerprint(row),
            )
        )
    row.status = status
    row.resolution = resolution
    row.resolved_by_user_id = resolved_by_user_id
    row.resolved_at = datetime.now(UTC)
    payload: dict[str, Any] = {"intervention_id": str(row.id), "status": status.value}
    # Fixed values only; a person's note stays on the request, never in events.
    for key in ("decision", "outcome", "then"):
        if key in resolution:
            payload[key] = resolution[key]
    if resolved_by_user_id is not None:
        payload["user_id"] = str(resolved_by_user_id)
    record_event(
        session,
        workflow,
        WorkflowEventType.INTERVENTION_RESOLVED,
        task_id=row.task_id,
        payload=payload,
    )


def close_interventions(
    session: Session,
    workflow: Workflow,
    *,
    status: InterventionStatus,
    resolution: dict[str, Any],
    subject: InterventionSubject | None = None,
    task_id: uuid.UUID | None = None,
    resolved_by_user_id: uuid.UUID | None = None,
    decided_role: WorkspaceRole | None = None,
) -> int:
    """Closes the open requests of a workflow (or one task, or one subject)."""
    statement = select(HumanIntervention).where(
        HumanIntervention.workflow_id == workflow.id,
        HumanIntervention.workspace_id == workflow.workspace_id,
        HumanIntervention.status == InterventionStatus.OPEN,
    )
    if subject is not None:
        statement = statement.where(HumanIntervention.subject == subject.value)
    if task_id is not None:
        statement = statement.where(HumanIntervention.task_id == task_id)
    # App sessions do not autoflush: a request closed earlier in this transaction still matches
    # the query, so its loaded status decides.
    rows = [r for r in session.execute(statement).scalars() if r.status == InterventionStatus.OPEN]
    for row in rows:
        resolve_intervention(
            session,
            workflow,
            row,
            status=status,
            resolution=resolution,
            resolved_by_user_id=resolved_by_user_id,
            decided_role=decided_role,
        )
    return len(rows)


def decide_agent_action(
    session: Session,
    workflow: Workflow,
    row: HumanIntervention,
    task: WorkflowTask,
    *,
    decision: str,
    stop: bool = False,
    note: str | None = None,
    user_id: uuid.UUID | None = None,
    role: WorkspaceRole | None = None,
    expired: bool = False,
) -> None:
    """Applies a decision on an agent's approval request (ADR-0035, ADR-0037), for a person or,
    when it expired, as a rejection by the platform. The attempt that asked counts: the next
    attempt follows it, by the same agent, unless that was the last one allowed or the admin
    stopped the task; both escalate it to people. A note is for people and never reaches agents.
    """
    resolution: dict[str, Any] = {"decision": decision}
    if stop:
        resolution["then"] = "stop"
    if note:
        resolution["note"] = note
    if expired:
        resolution["outcome"] = "expired"
    resolve_intervention(
        session,
        workflow,
        row,
        status=InterventionStatus.EXPIRED if expired else InterventionStatus.RESOLVED,
        resolution=resolution,
        resolved_by_user_id=user_id,
        decided_role=role,
    )
    recorded = session.execute(
        select(func.max(AgentExecution.attempt_number)).where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == workflow.id,
            AgentExecution.workspace_id == workflow.workspace_id,
        )
    ).scalar_one()
    task.attempt_count = max(task.attempt_count, int(recorded or 0))
    actor = Actor.USER if user_id is not None else Actor.SYSTEM
    details: dict[str, Any] = {"decision": decision, "attempt_count": task.attempt_count}
    if user_id is not None:
        details["user_id"] = str(user_id)
    if stop:
        transition_task(
            session,
            workflow,
            task,
            T.ESCALATED,
            actor=actor,
            reason=APPROVAL_REJECTED,
            extra={**details, "attempt_number": task.attempt_count},
        )
    elif task.attempt_count >= MAX_ATTEMPTS_PER_TASK:
        transition_task(
            session,
            workflow,
            task,
            T.ESCALATED,
            actor=Actor.SYSTEM,
            reason="ATTEMPTS_EXHAUSTED",
            extra={**details, "attempt_number": task.attempt_count},
        )
    else:
        transition_task(
            session,
            workflow,
            task,
            T.ASSIGNED,
            actor=actor,
            reason=APPROVAL_EXPIRED if expired else f"approval request: {decision}",
            extra={
                **details,
                "agent_id": str(task.assigned_agent_id),
                "agent_version_id": str(task.assigned_agent_version_id),
            },
        )


def _on_task_change(
    session: Session,
    workflow: Workflow,
    task: WorkflowTask,
    source: TaskStatus,
    target: TaskStatus,
    actor: Actor,
    reason: str | None,
    extra: dict[str, Any] | None,
) -> None:
    if source == T.ESCALATED:
        # People acted on the escalated task (reassigned, cancelled, removed it).
        user_id = (extra or {}).get("user_id")
        close_interventions(
            session,
            workflow,
            status=InterventionStatus.RESOLVED,
            resolution={"outcome": target.value, "actor": actor.value},
            subject=InterventionSubject.TASK_ESCALATION,
            task_id=task.id,
            resolved_by_user_id=uuid.UUID(user_id) if actor == Actor.USER and user_id else None,
        )
    if source == T.WAITING_APPROVAL:
        # A decision closes its request first; anything left (e.g. a cancellation) is withdrawn.
        close_interventions(
            session,
            workflow,
            status=InterventionStatus.WITHDRAWN,
            resolution={"outcome": target.value},
            subject=InterventionSubject.AGENT_ACTION,
            task_id=task.id,
        )
    if target == T.ESCALATED:
        details = {k: v for k, v in (extra or {}).items() if k in _CONTEXT_KEYS}
        open_intervention(
            session,
            workflow,
            type=InterventionType.MANUAL_RECOVERY,
            subject=InterventionSubject.TASK_ESCALATION,
            requested_action=_ESCALATION_ACTION,
            context={"reason": reason, **details},
            source_key=f"escalation:{task.id}:{task.revision}",
            task_id=task.id,
        )


def _on_workflow_change(
    session: Session, workflow: Workflow, source: WorkflowStatus, target: WorkflowStatus
) -> None:
    if source == W.AWAITING_APPROVAL:
        # A decision closes it first (ADR-0035); otherwise the plan changed or was cancelled.
        close_interventions(
            session,
            workflow,
            status=InterventionStatus.WITHDRAWN,
            resolution={"outcome": target.value},
            subject=InterventionSubject.PLAN_APPROVAL,
        )
    if target == W.AWAITING_APPROVAL:
        open_intervention(
            session,
            workflow,
            type=InterventionType.APPROVAL,
            subject=InterventionSubject.PLAN_APPROVAL,
            requested_action=_PLAN_APPROVAL_ACTION,
            required_role=WorkspaceRole.ADMIN,
            context={
                "plan_version": workflow.confirmed_plan_version,
                "revision": workflow.revision,
            },
            source_key=f"plan_approval:{workflow.revision}",
        )
    if target in WORKFLOW_TERMINAL:
        close_interventions(
            session,
            workflow,
            status=InterventionStatus.WITHDRAWN,
            resolution={"outcome": target.value},
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


def request_verification(session: _AddsRows, workflow: Workflow, task: WorkflowTask) -> uuid.UUID:
    """Writes a `task.verify` outbox row (identifiers only) in the transaction that moves the
    task to SUBMITTED, so every submission is verified by the platform (ADR-0030)."""
    return outbox.enqueue(
        session,
        outbox.TASK_VERIFY,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        payload={
            "workspace_id": str(workflow.workspace_id),
            "workflow_id": str(workflow.id),
            "task_id": str(task.id),
        },
    )


def request_evaluation(session: _AddsRows, workflow: Workflow, task: WorkflowTask) -> uuid.UUID:
    """Writes a `task.evaluate` outbox row (identifiers only): the semantic evaluation of a
    submission that passed its deterministic checks (ADR-0031)."""
    return outbox.enqueue(
        session,
        outbox.TASK_EVALUATE,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        payload={
            "workspace_id": str(workflow.workspace_id),
            "workflow_id": str(workflow.id),
            "task_id": str(task.id),
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


_ORCHESTRATION_MARKER = "agentflow.orchestration_requested"
_DISPATCH_MARKER = "agentflow.dispatch_requested"


def _transaction_marker(session: Session, key: str) -> set[uuid.UUID]:
    """Ids already handled in the session's current transaction. Rows flushed earlier in it are
    no longer in `session.new`, so deduplication is tied to the transaction object."""
    transaction = session.get_transaction()
    marker: tuple[object, set[uuid.UUID]] | None = session.info.get(key)
    if marker is None or marker[0] is not transaction:
        marker = (transaction, set())
        session.info[key] = marker
    return marker[1]


def request_orchestration(session: Session, workflow: Workflow) -> None:
    """Asks a worker to reassess the workflow with the orchestration graph (ADR-0025): one
    `workflow.orchestrate` outbox row per transaction that changes a status, written in that
    transaction. The job reads the committed state, so later requests supersede nothing."""
    marker = _transaction_marker(session, _ORCHESTRATION_MARKER)
    if workflow.id in marker:
        return
    marker.add(workflow.id)
    outbox.enqueue(
        session,
        outbox.WORKFLOW_ORCHESTRATE,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        payload={"workspace_id": str(workflow.workspace_id), "workflow_id": str(workflow.id)},
    )


def dispatch_order(task: WorkflowTask) -> tuple[int, datetime, str]:
    """Waiting tasks are dispatched highest priority first, then oldest first (ADR-0028)."""
    return (-task.priority, task.created_at, str(task.id))


def _dispatch_waiting(session: Session, workflow: Workflow) -> None:
    """Dispatches as many ASSIGNED tasks as the workflow has free execution slots, in
    `dispatch_order`. The others wait; a slot freeing up calls this again. Tasks dispatched
    earlier in the same transaction count as taking a slot, so one transaction never writes
    more dispatches than there are free slots. The worker's claim still enforces the limit,
    because dispatched tasks only take a slot once claimed."""
    if workflow.status != W.RUNNING:
        return
    tasks = _active(list_tasks(session, workflow))
    marker = _transaction_marker(session, _DISPATCH_MARKER)
    waiting = sorted(
        (t for t in tasks if t.status == T.ASSIGNED and t.id not in marker), key=dispatch_order
    )
    taken = sum(
        1
        for t in tasks
        if t.status in EXECUTION_SLOT_STATUSES or (t.status == T.ASSIGNED and t.id in marker)
    )
    for task in waiting[: max(workflow.max_parallel_tasks - taken, 0)]:
        marker.add(task.id)
        request_dispatch(session, workflow, task)


def transition_workflow(
    session: Session,
    workflow: Workflow,
    target: WorkflowStatus,
    *,
    actor: Actor,
    reason: str | None = None,
    user_id: uuid.UUID | None = None,
    pause_reason: PauseReason | None = None,
) -> None:
    """`user_id` names the person behind a USER transition. A pause records why (ADR-0036): a
    person's pause by default, otherwise the platform's `pause_reason`, which is required."""
    source = workflow.status
    if not workflow_transition_allowed(source, target, actor):
        raise InvalidTransitionError(
            details={"from": source.value, "to": target.value, "actor": actor.value}
        )
    if target == W.PAUSED and pause_reason is None:
        if actor != Actor.USER:
            raise ValueError("a platform pause needs a pause_reason")
        pause_reason = PauseReason.USER
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
    if user_id is not None:
        payload["user_id"] = str(user_id)
    if source == W.PAUSED and workflow.pause_reason is not None:
        payload["pause_reason"] = workflow.pause_reason.value
    if target == W.PAUSED and pause_reason is not None:  # set above for every pause
        payload["pause_reason"] = pause_reason.value
        workflow.pause_reason = pause_reason
        workflow.paused_at = now
        workflow.paused_by_user_id = user_id if pause_reason == PauseReason.USER else None
        workflow.pause_note = reason if pause_reason == PauseReason.USER else None
    else:
        workflow.pause_reason = None
        workflow.paused_at = None
        workflow.paused_by_user_id = None
        workflow.pause_note = None
    record_event(session, workflow, _workflow_event_type(source, target), payload=payload)
    _on_workflow_change(session, workflow, source, target)
    request_orchestration(session, workflow)
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
        # Readiness first, so tasks that became ready while paused compete for the free slots.
        reconcile_workflow(session, workflow)
        _dispatch_waiting(session, workflow)


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
    dispatch: bool = True,
) -> None:
    """`closing_workflow` is set only by transition_workflow when it cancels the leftover tasks
    of a workflow it has just moved to a terminal status. `dispatch=False` leaves the dispatch
    decision to the caller, which makes one decision for several tasks (reconcile_workflow)."""
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
    if target == T.COMPLETED:
        _require_platform_acceptance(session, workflow, task)
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
    _on_task_change(session, workflow, task, source, target, actor, reason, extra)
    request_orchestration(session, workflow)
    if target == T.SUBMITTED:
        request_verification(session, workflow, task)
    # Flush so later queries in this transaction (and DB constraints) see the change; app
    # sessions do not autoflush.
    session.flush()
    if dispatch and (
        target == T.ASSIGNED
        or (source in EXECUTION_SLOT_STATUSES and target not in EXECUTION_SLOT_STATUSES)
    ):
        _dispatch_waiting(session, workflow)
    if reconcile:
        reconcile_workflow(session, workflow)
    if source == T.WAITING_APPROVAL:
        resume_after_decisions(session, workflow)


# --- pausing for people's decisions (ADR-0036) --------------------------------------------------
# A workflow whose plan needs approval also stops while an agent's request waits for an admin,
# and the platform resumes it once every such request is closed. It never lifts a person's or
# the deadline's pause.


def pause_at_budget(session: Session, workflow: Workflow) -> bool:
    """Pauses a RUNNING workflow once its actual plus estimated spend reaches its budget
    (ADR-0040; handoff scenario 13). Only spend in the workflow's currency counts: a budget in
    another currency than the prices is not comparable and never enforced. Like the deadline,
    it pauses once: resuming past the budget is a person's deliberate choice."""
    if workflow.status != W.RUNNING or workflow.budget_limit is None:
        return False
    already = session.execute(
        select(WorkflowEvent.id)
        .where(
            WorkflowEvent.workflow_id == workflow.id,
            WorkflowEvent.workspace_id == workflow.workspace_id,
            WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_STATUS_CHANGED,
            WorkflowEvent.payload["reason"].astext == BUDGET_REACHED,
        )
        .limit(1)
    ).first()
    if already is not None:
        return False
    spend = costing.workflow_spend(session, workflow)
    if spend <= 0 or spend < workflow.budget_limit:
        return False
    transition_workflow(
        session,
        workflow,
        W.PAUSED,
        actor=Actor.SYSTEM,
        reason=BUDGET_REACHED,
        pause_reason=PauseReason.BUDGET_REACHED,
    )
    return True


def pause_for_decision(session: Session, workflow: Workflow) -> bool:
    """Pauses a RUNNING approval-required workflow for an agent's approval request."""
    if workflow.autonomy_policy != AutonomyPolicy.APPROVAL_REQUIRED or workflow.status != W.RUNNING:
        return False
    transition_workflow(
        session,
        workflow,
        W.PAUSED,
        actor=Actor.SYSTEM,
        reason=AWAITING_DECISION,
        pause_reason=PauseReason.AWAITING_DECISION,
    )
    return True


def resume_after_decisions(session: Session, workflow: Workflow) -> bool:
    """Resumes a workflow the platform paused for decisions once none is still awaited."""
    if workflow.status != W.PAUSED or workflow.pause_reason != PauseReason.AWAITING_DECISION:
        return False
    waiting = session.execute(
        select(HumanIntervention).where(
            HumanIntervention.workflow_id == workflow.id,
            HumanIntervention.workspace_id == workflow.workspace_id,
            HumanIntervention.subject == InterventionSubject.AGENT_ACTION.value,
            HumanIntervention.status == InterventionStatus.OPEN,
        )
    ).scalars()
    # Loaded status decides: a request closed earlier in this transaction may not be flushed.
    if any(row.status == InterventionStatus.OPEN for row in waiting):
        return False
    transition_workflow(session, workflow, W.RUNNING, actor=Actor.SYSTEM, reason=DECISIONS_MADE)
    return True


# Every verifier whose PASSED result a task's latest successful attempt needs (ADR-0034); the
# database trigger of migration 0023 enforces the same rule for any write.
ACCEPTING_VERIFIERS = frozenset({"deterministic", "semantic"})


def _require_platform_acceptance(session: Session, workflow: Workflow, task: WorkflowTask) -> None:
    latest = session.execute(
        select(AgentExecution.id)
        .where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == workflow.id,
            AgentExecution.workspace_id == workflow.workspace_id,
            AgentExecution.status == ExecutionStatus.SUCCEEDED,
        )
        .order_by(AgentExecution.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    passed = (
        {
            verifier.split("/")[0]
            for verifier in session.execute(
                select(VerificationResult.verifier).where(
                    VerificationResult.execution_id == latest,
                    VerificationResult.workspace_id == workflow.workspace_id,
                    VerificationResult.outcome == VerificationOutcome.PASSED,
                )
            ).scalars()
        }
        if latest is not None
        else set()
    )
    if not ACCEPTING_VERIFIERS <= passed:
        raise TransitionPreconditionError(
            "Only platform verification can accept a task: its latest attempt has not passed."
        )


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
    pre_assigned = False
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
            if task.assigned_agent_version_id is not None:
                # Pre-assigned while waiting (ADR-0028): schedule it now, within the slot limit.
                # Dispatch is decided once below, so several ready tasks compete by priority.
                pre_assigned = True
                transition_task(
                    session,
                    workflow,
                    task,
                    T.ASSIGNED,
                    actor=Actor.SCHEDULER,
                    reason="agent pre-assigned",
                    extra={
                        "agent_id": str(task.assigned_agent_id),
                        "agent_version_id": str(task.assigned_agent_version_id),
                        "pre_assigned": True,
                    },
                    reconcile=False,
                    dispatch=False,
                )
    if pre_assigned:
        _dispatch_waiting(session, workflow)

    if any(t.required and t.status in _BLOCKING_REQUIRED for t in tasks):
        transition_workflow(
            session, workflow, W.BLOCKED, actor=Actor.SYSTEM, reason="a required task is blocked"
        )


def running_count(session: Session, workflow: Workflow) -> int:
    """Tasks currently occupying an execution slot."""
    return sum(
        1 for t in _active(list_tasks(session, workflow)) if t.status in EXECUTION_SLOT_STATUSES
    )
