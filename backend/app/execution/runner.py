"""Task execution for Celery workers (ADR-0010). No LLM yet: `run_mock_agent` is harmless.

An execution is three bounded steps; no transaction stays open while the agent works:

1. `claim`  - one transaction: record the event in the inbox (a redelivered or duplicate event
              stops here), check the task is still ASSIGNED to the dispatched agent version and
              the workflow is RUNNING with a free slot, then ASSIGNED -> QUEUED -> IN_PROGRESS
              and insert an `agent_executions` row for this attempt.
2. work     - outside any transaction.
3. `finish` - one transaction: IN_PROGRESS -> SUBMITTED (agents never complete tasks; platform
              verification does), or, on failure/timeout, REWORK_REQUIRED (FAILED after the last
              allowed attempt). The execution row records the outcome; earlier attempts are kept.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution import outbox
from app.models import (
    AgentExecution,
    AgentVersion,
    ExecutionStatus,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.workflows import engine
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.execution")

CONSUMER = "execute_task"
# Handoff section 6: at most 3 attempts per task.
MAX_ATTEMPTS_PER_TASK = 3


class Skipped(StrEnum):
    DUPLICATE = "duplicate"  # this event was already consumed
    STALE = "stale"  # the task moved on, or was reassigned to another version
    NOT_RUNNING = "not_running"  # workflow paused/blocked/finished; resume re-dispatches
    DEFERRED = "deferred"  # no free slot; a finishing task re-dispatches it


@dataclass(frozen=True)
class Claim:
    execution_id: uuid.UUID
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    task_title: str
    agent_version: int
    attempt_number: int


@dataclass(frozen=True)
class Failure:
    code: str
    summary: str
    timed_out: bool = False


def _lock_workflow(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow | None:
    statement = (
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    )
    return session.execute(statement).scalar_one_or_none()


def _get_task(session: Session, workflow: Workflow, task_id: uuid.UUID) -> WorkflowTask | None:
    statement = select(WorkflowTask).where(
        WorkflowTask.id == task_id,
        WorkflowTask.workflow_id == workflow.id,
        WorkflowTask.workspace_id == workflow.workspace_id,
    )
    return session.execute(statement).scalar_one_or_none()


def claim(
    session: Session,
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    agent_version_id: uuid.UUID,
) -> Claim | Skipped:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return Skipped.DUPLICATE
    workflow = _lock_workflow(session, workspace_id, workflow_id)
    task = _get_task(session, workflow, task_id) if workflow else None
    if (
        workflow is None
        or task is None
        or task.status != TaskStatus.ASSIGNED
        or task.assigned_agent_version_id != agent_version_id
        or task.assigned_agent_id is None
    ):
        session.commit()  # consumed: this event will never be acted on
        return Skipped.STALE
    if workflow.status != WorkflowStatus.RUNNING:
        session.commit()
        return Skipped.NOT_RUNNING
    if engine.running_count(session, workflow) >= workflow.max_parallel_tasks:
        session.commit()
        return Skipped.DEFERRED

    version = session.get(AgentVersion, agent_version_id)
    if version is None:  # protected by the composite FK on the task
        session.commit()
        return Skipped.STALE
    attempt_number = task.attempt_count + 1
    engine.transition_task(
        session,
        workflow,
        task,
        TaskStatus.QUEUED,
        actor=Actor.SCHEDULER,
        reconcile=False,
        extra={"event_id": str(event_id)},
    )
    engine.transition_task(
        session,
        workflow,
        task,
        TaskStatus.IN_PROGRESS,
        actor=Actor.WORKER,
        reconcile=False,
        extra={"attempt_number": attempt_number},
    )
    execution = AgentExecution(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        agent_id=task.assigned_agent_id,
        agent_version_id=agent_version_id,
        attempt_number=attempt_number,
        status=ExecutionStatus.RUNNING,
        dispatch_event_id=event_id,
    )
    session.add(execution)
    session.commit()
    return Claim(
        execution_id=execution.id,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        task_title=task.title,
        agent_version=version.version,
        attempt_number=attempt_number,
    )


def run_mock_agent(claim: Claim) -> str:
    """Stand-in for the agent runtime (Phase 3). Harmless and deterministic: no LLM, no tools."""
    return (
        f"Mock result for task '{claim.task_title}' "
        f"(agent version {claim.agent_version}, attempt {claim.attempt_number})."
    )


def finish(session: Session, claim: Claim, *, result: str | None, failure: Failure | None) -> str:
    workflow = _lock_workflow(session, claim.workspace_id, claim.workflow_id)
    execution = session.get(AgentExecution, claim.execution_id, with_for_update=True)
    task = _get_task(session, workflow, claim.task_id) if workflow else None
    if workflow is None or execution is None or task is None:
        session.rollback()
        return "missing"
    if execution.status != ExecutionStatus.RUNNING:
        session.rollback()
        return "already_finished"
    execution.completed_at = datetime.now(UTC)
    if task.status != TaskStatus.IN_PROGRESS:
        # Cancelled (or otherwise moved on) while the agent was working.
        execution.status = ExecutionStatus.CANCELLED
        session.commit()
        return "cancelled"
    context = {"execution_id": str(execution.id), "attempt_number": claim.attempt_number}
    if failure is None:
        execution.status = ExecutionStatus.SUCCEEDED
        execution.result_summary = result
        engine.transition_task(
            session, workflow, task, TaskStatus.SUBMITTED, actor=Actor.WORKER, extra=context
        )
        outcome = "submitted"
    else:
        execution.status = (
            ExecutionStatus.TIMED_OUT if failure.timed_out else ExecutionStatus.FAILED
        )
        execution.error_code = failure.code
        execution.error_summary = failure.summary[:1000]
        target = (
            TaskStatus.REWORK_REQUIRED
            if claim.attempt_number < MAX_ATTEMPTS_PER_TASK
            else TaskStatus.FAILED
        )
        engine.transition_task(
            session, workflow, task, target, actor=Actor.SYSTEM, reason=failure.code, extra=context
        )
        outcome = target.value.lower()
    session.commit()
    return outcome
