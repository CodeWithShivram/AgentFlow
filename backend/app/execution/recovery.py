"""Recovery of work stranded by a lost worker or message (ADR-0029; resolves OD-17).

Delivery is at least once, and every consumer records its event in the inbox when it claims it.
A worker that dies after its claim committed therefore leaves work that no redelivery picks up:
the inbox skips the redelivered message as a duplicate. Beat runs `recover_once` every minute.
Each item is recovered in its own short transaction, under the workflow lock and through the
engine, so every status change writes its event:

- Lost attempts: an execution still RUNNING `LOST_ATTEMPT_AFTER` after it started (the worker's
  hard time limit plus a grace period) fails with WORKER_LOST. The task is retried automatically
  with its pinned agent version, within the slot limit; the lost attempt counts toward the
  three, so after the last one the task FAILS.
- Lost dispatches: an ASSIGNED task in a RUNNING workflow whose latest dispatch was published
  `LOST_DISPATCH_AFTER` ago and never claimed (the broker lost the message) is dispatched again,
  within the slot limit. A late original delivery finds the task moved on and does nothing.
- Lost verifications: a SUBMITTED task whose latest `task.verify` was published
  `LOST_DISPATCH_AFTER` ago and never consumed is sent for verification again (ADR-0030); likewise
  a VERIFYING task's lost `task.evaluate` (ADR-0031).
- Interrupted evaluations: a semantic evaluation still pending `INTERRUPTED_EVALUATION_AFTER`
  after its job claimed it escalates the task (EVALUATION_INTERRUPTED) for people to decide.
- Deadlines (ADR-0034): a RUNNING workflow past its deadline is paused (SYSTEM, DEADLINE_PASSED)
  once; running attempts finish, and people decide to resume, replan or cancel. A workflow that
  people resumed after that pause is not paused again.
- Interrupted planning jobs: an analysis or plan request still PENDING `INTERRUPTED_JOB_AFTER`
  after its job claimed it is marked FAILED, so the user can retry it.
- Expired requests (ADR-0037): an agent's approval request still OPEN after its `expires_at` is
  EXPIRED and handled as a rejection (the agent finishes without the action), as the engine
  handles an admin's rejection; a workflow paused for it then resumes (ADR-0036).

A slow worker whose attempt was recovered finds it no longer current: its agent stops at the
next model or tool call, and `finish` discards its result.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox, runner
from app.models import (
    AgentExecution,
    AnalysisStatus,
    ExecutionStatus,
    HumanIntervention,
    InboxMessage,
    InterventionStatus,
    InterventionSubject,
    OutboxEvent,
    OutboxStatus,
    PauseReason,
    PlanStatus,
    TaskStatus,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
from app.planning import analysis, plan
from app.verification import job as verification_job
from app.verification import rework, semantic
from app.workflows import engine
from app.workflows.state_machine import WORKFLOW_EXECUTING, Actor

logger = logging.getLogger("agentflow.recovery")

WORKER_LOST = "WORKER_LOST"
ANALYSIS_INTERRUPTED = "ANALYSIS_INTERRUPTED"
PLAN_GENERATION_INTERRUPTED = "PLAN_GENERATION_INTERRUPTED"
# Time for a worker that is still alive to record its outcome after the hard limit.
GRACE = timedelta(seconds=120)
LOST_ATTEMPT_AFTER = timedelta(seconds=runner.AGENT_HARD_TIME_LIMIT_SECONDS) + GRACE
LOST_DISPATCH_AFTER = timedelta(minutes=15)
INTERRUPTED_JOB_AFTER = timedelta(seconds=analysis.JOB_TIME_LIMIT_SECONDS) + GRACE
INTERRUPTED_EVALUATION_AFTER = timedelta(seconds=semantic.JOB_TIME_LIMIT_SECONDS) + GRACE
EVALUATION_INTERRUPTED = "EVALUATION_INTERRUPTED"
DEADLINE_PASSED = "DEADLINE_PASSED"
BATCH_SIZE = 100


@dataclass
class RecoveryReport:
    lost_attempts: int = 0
    retried: int = 0
    redispatched: int = 0
    reverified: int = 0
    reevaluated: int = 0
    interrupted_evaluations: int = 0
    paused_at_deadline: int = 0
    interrupted_jobs: int = 0
    expired_requests: int = 0


def recover_once(
    factory: sessionmaker[Session], *, now: datetime | None = None, batch_size: int = BATCH_SIZE
) -> RecoveryReport:
    now = now or datetime.now(UTC)
    report = RecoveryReport()
    with session_scope(factory) as session:
        lost = _lost_attempts(session, now - LOST_ATTEMPT_AFTER, batch_size)
    for workspace_id, workflow_id, execution_id in lost:
        with session_scope(factory) as session:
            outcome = recover_attempt(session, workspace_id, workflow_id, execution_id, now=now)
        if outcome is not None:
            report.lost_attempts += 1
            report.retried += outcome == "retried"

    with session_scope(factory) as session:
        stalled = _stalled_dispatches(session, now - LOST_DISPATCH_AFTER, batch_size)
    for (workspace_id, workflow_id), task_ids in stalled.items():
        with session_scope(factory) as session:
            report.redispatched += redispatch(session, workspace_id, workflow_id, task_ids)

    with session_scope(factory) as session:
        unverified = _stalled_verifications(session, now - LOST_DISPATCH_AFTER, batch_size)
    for (workspace_id, workflow_id), task_ids in unverified.items():
        with session_scope(factory) as session:
            report.reverified += reverify(session, workspace_id, workflow_id, task_ids)

    with session_scope(factory) as session:
        unevaluated = _stalled_evaluations(session, now - LOST_DISPATCH_AFTER, batch_size)
    for (workspace_id, workflow_id), task_ids in unevaluated.items():
        with session_scope(factory) as session:
            report.reevaluated += reevaluate(session, workspace_id, workflow_id, task_ids)

    with session_scope(factory) as session:
        interrupted_evaluations = _interrupted_evaluations(
            session, now - INTERRUPTED_EVALUATION_AFTER, batch_size
        )
    for workspace_id, workflow_id, task_id in interrupted_evaluations:
        with session_scope(factory) as session:
            report.interrupted_evaluations += escalate_interrupted_evaluation(
                session, workspace_id, workflow_id, task_id
            )

    with session_scope(factory) as session:
        overdue = _overdue_workflows(session, now, batch_size)
    for workspace_id, workflow_id in overdue:
        with session_scope(factory) as session:
            report.paused_at_deadline += pause_at_deadline(
                session, workspace_id, workflow_id, now=now
            )

    with session_scope(factory) as session:
        interrupted = _interrupted_jobs(session, now - INTERRUPTED_JOB_AFTER, batch_size)
    for kind, workspace_id, workflow_id, request_id in interrupted:
        with session_scope(factory) as session:
            report.interrupted_jobs += fail_interrupted_job(
                session, kind, workspace_id, workflow_id, request_id
            )

    with session_scope(factory) as session:
        expired = _expired_requests(session, now, batch_size)
    for workspace_id, workflow_id, intervention_id in expired:
        with session_scope(factory) as session:
            report.expired_requests += expire_request(
                session, workspace_id, workflow_id, intervention_id, now=now
            )
    return report


def _lock_workflow(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow | None:
    statement = (
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    )
    return session.execute(statement).scalar_one_or_none()


# --- lost attempts -----------------------------------------------------------------------------


def _lost_attempts(
    session: Session, started_before: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    rows = session.execute(
        select(AgentExecution.workspace_id, AgentExecution.workflow_id, AgentExecution.id)
        .where(
            AgentExecution.status == ExecutionStatus.RUNNING,
            AgentExecution.started_at < started_before,
        )
        .order_by(AgentExecution.started_at)
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in rows]


def recover_attempt(
    session: Session,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    execution_id: uuid.UUID,
    *,
    now: datetime,
) -> str | None:
    """Closes one lost attempt and retries or fails its task. Returns None if the attempt
    finished meanwhile."""
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    execution = session.get(AgentExecution, execution_id, with_for_update=True)
    if workflow is None or execution is None or execution.status != ExecutionStatus.RUNNING:
        session.rollback()
        return None
    task = session.execute(
        select(WorkflowTask).where(
            WorkflowTask.id == execution.task_id,
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.workspace_id == workflow.workspace_id,
        )
    ).scalar_one()
    execution.completed_at = now
    if task.status != TaskStatus.IN_PROGRESS:
        # The task was cancelled (or moved on) while the attempt was lost, as `finish` handles it.
        execution.status = ExecutionStatus.CANCELLED
        session.commit()
        return "cancelled"

    execution.status = ExecutionStatus.FAILED
    execution.error_code = WORKER_LOST
    execution.error_summary = "The worker running this attempt stopped before it finished."
    context = {"execution_id": str(execution.id), "attempt_number": execution.attempt_number}
    if execution.attempt_number >= runner.MAX_ATTEMPTS_PER_TASK:
        # Attempts exhausted: people decide (ADR-0034).
        engine.transition_task(
            session,
            workflow,
            task,
            TaskStatus.ESCALATED,
            actor=Actor.SYSTEM,
            reason=WORKER_LOST,
            extra=context,
        )
        outcome = "escalated"
    else:
        engine.transition_task(
            session,
            workflow,
            task,
            TaskStatus.REWORK_REQUIRED,
            actor=Actor.SYSTEM,
            reason=WORKER_LOST,
            extra=context,
            reconcile=False,
        )
        # The next attempt through the retry policy (ADR-0032). A lost worker is not the
        # agent's failure, so it never leads to a reassignment.
        outcome = rework.schedule_rework(session, workflow, task, failure_codes=[WORKER_LOST])
    session.commit()
    logger.warning(
        "lost attempt recovered",
        extra={"execution_id": str(execution_id), "task_id": str(task.id), "outcome": outcome},
    )
    return outcome


# --- lost dispatches ---------------------------------------------------------------------------


def _stalled(
    session: Session,
    *,
    event_type: str,
    consumer: str,
    task_status: TaskStatus,
    workflow_statuses: frozenset[WorkflowStatus],
    published_before: datetime,
    limit: int,
) -> dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]]:
    """Tasks in `task_status` whose latest `event_type` event was published long ago and never
    consumed by `consumer`, grouped by workflow."""
    newer = aliased(OutboxEvent)
    rows = session.execute(
        select(WorkflowTask.workspace_id, WorkflowTask.workflow_id, WorkflowTask.id)
        .join(
            OutboxEvent,
            and_(
                OutboxEvent.task_id == WorkflowTask.id,
                OutboxEvent.workflow_id == WorkflowTask.workflow_id,
                OutboxEvent.event_type == event_type,
            ),
        )
        .join(
            Workflow,
            and_(
                Workflow.id == WorkflowTask.workflow_id,
                Workflow.workspace_id == WorkflowTask.workspace_id,
            ),
        )
        .where(
            Workflow.status.in_(workflow_statuses),
            WorkflowTask.status == task_status,
            WorkflowTask.removed_at.is_(None),
            OutboxEvent.status == OutboxStatus.PUBLISHED,
            OutboxEvent.published_at < published_before,
            ~exists().where(
                InboxMessage.event_id == OutboxEvent.id,
                InboxMessage.consumer == consumer,
            ),
            ~exists().where(
                newer.task_id == OutboxEvent.task_id,
                newer.event_type == event_type,
                newer.created_at > OutboxEvent.created_at,
            ),
        )
        .order_by(OutboxEvent.published_at)
        .limit(limit)
    )
    stalled: dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]] = {}
    for workspace_id, workflow_id, task_id in rows:
        stalled.setdefault((workspace_id, workflow_id), []).append(task_id)
    return stalled


def _stalled_dispatches(
    session: Session, published_before: datetime, limit: int
) -> dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]]:
    """ASSIGNED tasks in RUNNING workflows whose latest dispatch was never claimed."""
    return _stalled(
        session,
        event_type=outbox.TASK_DISPATCH,
        consumer=runner.CONSUMER,
        task_status=TaskStatus.ASSIGNED,
        workflow_statuses=frozenset({WorkflowStatus.RUNNING}),
        published_before=published_before,
        limit=limit,
    )


def redispatch(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID, task_ids: list[uuid.UUID]
) -> int:
    """Dispatches the stalled tasks again, highest priority first, within the free slots."""
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    if workflow is None or workflow.status != WorkflowStatus.RUNNING:
        session.rollback()
        return 0
    wanted = set(task_ids)
    tasks = [t for t in engine.list_tasks(session, workflow) if t.removed_at is None]
    stalled = sorted(
        (t for t in tasks if t.id in wanted and t.status == TaskStatus.ASSIGNED),
        key=engine.dispatch_order,
    )
    free = workflow.max_parallel_tasks - engine.running_count(session, workflow)
    chosen = stalled[: max(free, 0)]
    for task in chosen:
        engine.request_dispatch(session, workflow, task)
    session.commit()
    if chosen:
        logger.warning(
            "stalled dispatches resent",
            extra={"workflow_id": str(workflow_id), "count": len(chosen)},
        )
    return len(chosen)


# --- lost verifications ------------------------------------------------------------------------


def _stalled_verifications(
    session: Session, published_before: datetime, limit: int
) -> dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]]:
    """SUBMITTED tasks whose latest verification request was never consumed. Verification runs
    whether the workflow is running, paused or blocked."""
    return _stalled(
        session,
        event_type=outbox.TASK_VERIFY,
        consumer=verification_job.CONSUMER,
        task_status=TaskStatus.SUBMITTED,
        workflow_statuses=WORKFLOW_EXECUTING,
        published_before=published_before,
        limit=limit,
    )


def reverify(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID, task_ids: list[uuid.UUID]
) -> int:
    """Requests verification again for tasks that are still SUBMITTED."""
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    if workflow is None or workflow.status not in WORKFLOW_EXECUTING:
        session.rollback()
        return 0
    wanted = set(task_ids)
    chosen = [
        t
        for t in engine.list_tasks(session, workflow)
        if t.id in wanted and t.status == TaskStatus.SUBMITTED
    ]
    for task in chosen:
        engine.request_verification(session, workflow, task)
    session.commit()
    if chosen:
        logger.warning(
            "stalled verifications resent",
            extra={"workflow_id": str(workflow_id), "count": len(chosen)},
        )
    return len(chosen)


# --- semantic evaluations ----------------------------------------------------------------------


def _stalled_evaluations(
    session: Session, published_before: datetime, limit: int
) -> dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]]:
    """VERIFYING tasks whose latest evaluation request was never consumed."""
    return _stalled(
        session,
        event_type=outbox.TASK_EVALUATE,
        consumer=semantic.CONSUMER,
        task_status=TaskStatus.VERIFYING,
        workflow_statuses=WORKFLOW_EXECUTING,
        published_before=published_before,
        limit=limit,
    )


def reevaluate(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID, task_ids: list[uuid.UUID]
) -> int:
    """Requests the evaluation again for tasks whose attempt still awaits it."""
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    if workflow is None or workflow.status not in WORKFLOW_EXECUTING:
        session.rollback()
        return 0
    chosen = []
    for task_id in task_ids:
        pending = semantic.pending_attempt(session, workflow, task_id)
        if pending is not None:
            engine.request_evaluation(session, workflow, pending[0])
            chosen.append(task_id)
    session.commit()
    if chosen:
        logger.warning(
            "stalled evaluations resent",
            extra={"workflow_id": str(workflow_id), "count": len(chosen)},
        )
    return len(chosen)


def _interrupted_evaluations(
    session: Session, claimed_before: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """VERIFYING tasks whose latest evaluation request was claimed long ago."""
    newer = aliased(OutboxEvent)
    rows = session.execute(
        select(WorkflowTask.workspace_id, WorkflowTask.workflow_id, WorkflowTask.id)
        .join(
            OutboxEvent,
            and_(
                OutboxEvent.task_id == WorkflowTask.id,
                OutboxEvent.workflow_id == WorkflowTask.workflow_id,
                OutboxEvent.event_type == outbox.TASK_EVALUATE,
            ),
        )
        .join(
            InboxMessage,
            and_(
                InboxMessage.event_id == OutboxEvent.id,
                InboxMessage.consumer == semantic.CONSUMER,
            ),
        )
        .where(
            WorkflowTask.status == TaskStatus.VERIFYING,
            InboxMessage.processed_at < claimed_before,
            ~exists().where(
                newer.task_id == OutboxEvent.task_id,
                newer.event_type == outbox.TASK_EVALUATE,
                newer.created_at > OutboxEvent.created_at,
            ),
        )
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in rows]


def escalate_interrupted_evaluation(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> int:
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    pending = semantic.pending_attempt(session, workflow, task_id) if workflow else None
    if workflow is None or pending is None:
        session.rollback()
        return 0
    semantic.escalate_unavailable(
        session, workflow, pending[0], code=EVALUATION_INTERRUPTED, attempts=0
    )
    session.commit()
    logger.warning("interrupted evaluation escalated", extra={"task_id": str(task_id)})
    return 1


# --- deadlines ----------------------------------------------------------------------------------


def _paused_for_deadline(workflow_id: Any) -> Any:
    """Whether the workflow was already paused for its deadline; workflow_id is an id or the
    workflows.id column (in a correlated query)."""
    return exists().where(
        WorkflowEvent.workflow_id == workflow_id,
        WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_STATUS_CHANGED,
        WorkflowEvent.payload["reason"].astext == DEADLINE_PASSED,
    )


def _overdue_workflows(
    session: Session, now: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = session.execute(
        select(Workflow.workspace_id, Workflow.id)
        .where(
            Workflow.status == WorkflowStatus.RUNNING,
            Workflow.deadline.is_not(None),
            Workflow.deadline < now,
            ~_paused_for_deadline(Workflow.id),
        )
        .order_by(Workflow.deadline)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in rows]


def pause_at_deadline(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID, *, now: datetime
) -> int:
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    if (
        workflow is None
        or workflow.status != WorkflowStatus.RUNNING
        or workflow.deadline is None
        or workflow.deadline >= now
        or session.execute(select(_paused_for_deadline(workflow.id))).scalar_one()
    ):
        session.rollback()
        return 0
    engine.transition_workflow(
        session,
        workflow,
        WorkflowStatus.PAUSED,
        actor=Actor.SYSTEM,
        reason=DEADLINE_PASSED,
        pause_reason=PauseReason.DEADLINE_PASSED,
    )
    session.commit()
    logger.warning("workflow paused at its deadline", extra={"workflow_id": str(workflow_id)})
    return 1


# --- expired requests --------------------------------------------------------------------------


def _expired_requests(
    session: Session, now: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    rows = session.execute(
        select(HumanIntervention.workspace_id, HumanIntervention.workflow_id, HumanIntervention.id)
        .where(
            HumanIntervention.status == InterventionStatus.OPEN,
            HumanIntervention.subject == InterventionSubject.AGENT_ACTION.value,
            HumanIntervention.expires_at < now,
        )
        .order_by(HumanIntervention.expires_at)
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in rows]


def expire_request(
    session: Session,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    intervention_id: uuid.UUID,
    *,
    now: datetime,
) -> int:
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    row = (
        session.execute(
            select(HumanIntervention)
            .where(
                HumanIntervention.id == intervention_id,
                HumanIntervention.workflow_id == workflow_id,
                HumanIntervention.workspace_id == workspace_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if workflow
        else None
    )
    task = (
        session.execute(
            select(WorkflowTask).where(
                WorkflowTask.id == row.task_id,
                WorkflowTask.workflow_id == workflow_id,
                WorkflowTask.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if workflow is not None and row is not None and row.task_id is not None
        else None
    )
    if (
        workflow is None
        or row is None
        or task is None
        or row.status != InterventionStatus.OPEN
        or row.expires_at is None
        or row.expires_at >= now
        or task.status != TaskStatus.WAITING_APPROVAL
    ):
        session.rollback()  # decided or withdrawn meanwhile
        return 0
    engine.decide_agent_action(session, workflow, row, task, decision="reject", expired=True)
    session.commit()
    logger.info(
        "approval request expired",
        extra={"workflow_id": str(workflow_id), "intervention_id": str(intervention_id)},
    )
    return 1


# --- interrupted planning jobs -----------------------------------------------------------------


def _interrupted_jobs(
    session: Session, claimed_before: datetime, limit: int
) -> list[tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]]:
    found: list[tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]] = []
    for kind, status_column, pending, request_column, consumer in (
        (
            "analysis",
            Workflow.analysis_status,
            AnalysisStatus.PENDING,
            Workflow.analysis_request_id,
            analysis.CONSUMER,
        ),
        ("plan", Workflow.plan_status, PlanStatus.PENDING, Workflow.plan_request_id, plan.CONSUMER),
    ):
        rows = session.execute(
            select(Workflow.workspace_id, Workflow.id, request_column)
            .join(
                InboxMessage,
                and_(InboxMessage.event_id == request_column, InboxMessage.consumer == consumer),
            )
            .where(status_column == pending, InboxMessage.processed_at < claimed_before)
            .limit(limit)
        )
        found.extend((kind, row[0], row[1], row[2]) for row in rows if row[2] is not None)
    return found


def fail_interrupted_job(
    session: Session,
    kind: str,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    request_id: uuid.UUID,
) -> int:
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    if workflow is None:
        session.rollback()
        return 0
    if kind == "analysis":
        if not (
            workflow.analysis_status == AnalysisStatus.PENDING
            and workflow.analysis_request_id == request_id
        ):
            session.rollback()
            return 0
        analysis.record_failure(
            session,
            workflow,
            request_id,
            analysis.AnalysisFailure(code=ANALYSIS_INTERRUPTED, attempts=0),
        )
    else:
        if not (
            workflow.plan_status == PlanStatus.PENDING and workflow.plan_request_id == request_id
        ):
            session.rollback()
            return 0
        plan.record_failure(
            session,
            workflow,
            request_id,
            plan.PlanFailure(code=PLAN_GENERATION_INTERRUPTED, attempts=0),
        )
    session.commit()
    logger.warning(
        "interrupted planning job failed",
        extra={"workflow_id": str(workflow_id), "kind": kind},
    )
    return 1
