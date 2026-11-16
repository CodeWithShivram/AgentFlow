"""Platform verification of a submitted attempt (ADR-0030), run by workers.

The engine writes `task.verify` in the transaction that moves a task to SUBMITTED. The job is
one short transaction with no model call: consume the event (a duplicate stops here), lock the
workflow, check the task is still SUBMITTED, run the deterministic checks on its latest
successful attempt and store the result, then move the task SUBMITTED -> VERIFYING. When the
checks pass, it requests the semantic evaluation (`task.evaluate`, ADR-0031), which decides; when
they do not, the task goes -> REWORK_REQUIRED (FAILED after the last allowed attempt). Only the
verification jobs, as the VERIFIER actor, accept tasks (handoff 5.3). The inbox row and the
outcome commit together, so a worker lost mid-way leaves nothing behind: the redelivery runs
again.
"""

import logging
import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution import outbox
from app.execution.runner import MAX_ATTEMPTS_PER_TASK
from app.models import (
    AgentExecution,
    AgentVersion,
    ExecutionStatus,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowTask,
)
from app.verification import deterministic, rework
from app.workflows import engine
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.verification")

CONSUMER = "verify_task"


class Skipped(StrEnum):
    DUPLICATE = "duplicate"  # this event was already consumed
    STALE = "stale"  # the task is no longer SUBMITTED (cancelled, or verified already)


def _latest_submission(session: Session, task: WorkflowTask) -> AgentExecution | None:
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
    *,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
) -> str:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return Skipped.DUPLICATE.value
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
        if workflow
        else None
    )
    execution = _latest_submission(session, task) if task else None
    if workflow is None or task is None or task.status != TaskStatus.SUBMITTED or not execution:
        session.commit()  # consumed: this event will never be acted on
        return Skipped.STALE.value

    version = session.get(AgentVersion, execution.agent_version_id)
    report = deterministic.verify(
        deterministic.Subject(
            submission=execution.submission,
            acceptance_criteria=list(task.acceptance_criteria),
            deliverables=list(task.deliverables),
            output_schema=task.expected_output_schema,
            configuration=version.configuration if version else {},
        )
    )
    result = VerificationResult(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        execution_id=execution.id,
        verifier=deterministic.VERIFIER,
        outcome=VerificationOutcome.PASSED if report.passed else VerificationOutcome.FAILED,
        checks=report.checks_json(),
        issues=report.issues_json(),
    )
    session.add(result)
    context = {
        "execution_id": str(execution.id),
        "attempt_number": execution.attempt_number,
        "verification_id": str(result.id),
        "verifier": deterministic.VERIFIER,
    }
    engine.transition_task(
        session,
        workflow,
        task,
        TaskStatus.VERIFYING,
        actor=Actor.VERIFIER,
        extra=context,
        reconcile=False,
    )
    if report.passed:
        # The task stays VERIFYING until the semantic evaluation decides (ADR-0031).
        engine.request_evaluation(session, workflow, task)
        outcome = "passed"
    else:
        target = (
            TaskStatus.REWORK_REQUIRED
            if execution.attempt_number < MAX_ATTEMPTS_PER_TASK
            else TaskStatus.ESCALATED  # attempts exhausted: people decide (ADR-0034)
        )
        engine.transition_task(
            session,
            workflow,
            task,
            target,
            actor=Actor.VERIFIER,
            reason="VERIFICATION_FAILED",
            extra={**context, "issue_codes": report.error_codes()},
        )
        outcome = target.value.lower()
        if target == TaskStatus.REWORK_REQUIRED:
            rework.schedule_rework(session, workflow, task, failure_codes=report.error_codes())
    session.commit()
    logger.info(
        "task verified",
        extra={"task_id": str(task.id), "verifier": deterministic.VERIFIER, "outcome": outcome},
    )
    return outcome
