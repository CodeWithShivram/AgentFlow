"""Celery tasks. Thin wrappers: the logic lives in app.execution (runner, relay, cleanup)."""

import logging
import time
import uuid

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import OperationalError

from app.db.sync_session import get_worker_session_factory, session_scope
from app.execution import cleanup, outbox, relay, runner
from app.llm.gateway import get_model_gateway
from app.planning import analysis, plan
from app.worker.celery_app import EXECUTION_QUEUE, celery_app, settings

logger = logging.getLogger("agentflow.worker")

# Outbox event type -> (Celery task name, queue).
EVENT_ROUTES = {
    outbox.TASK_DISPATCH: ("agentflow.execute_task", EXECUTION_QUEUE),
    outbox.WORKFLOW_ANALYZE: ("agentflow.analyze_workflow", EXECUTION_QUEUE),
    outbox.WORKFLOW_PLAN: ("agentflow.plan_workflow", EXECUTION_QUEUE),
}
_FINISH_ATTEMPTS = 3
# Default agent timeout (handoff section 6): 600 s. Hard limit gives the soft one room to finish.
EXECUTION_SOFT_TIME_LIMIT = 600
EXECUTION_TIME_LIMIT = 660
# Prompt analysis and plan generation: three model attempts with short backoff.
ANALYSIS_SOFT_TIME_LIMIT = 300
ANALYSIS_TIME_LIMIT = 330


def publish_event(event: outbox.ClaimedEvent) -> None:
    """Hands one outbox event to the broker. Raises if the broker does not accept it."""
    route = EVENT_ROUTES.get(event.event_type)
    if route is None:
        raise ValueError(f"no route for outbox event type {event.event_type!r}")
    task_name, queue = route
    celery_app.send_task(
        task_name,
        kwargs={**event.payload, "event_id": str(event.id)},
        task_id=str(event.id),  # the stable event ID doubles as the message ID
        queue=queue,
        retry=False,  # failures are recorded on the outbox row and retried by the relay
    )


@celery_app.task(name="agentflow.relay_outbox", ignore_result=True)
def relay_outbox() -> dict[str, int]:
    report = relay.relay_once(
        get_worker_session_factory(),
        publish_event,
        batch_size=settings.outbox_batch_size,
        lease_seconds=settings.outbox_lease_seconds,
        max_attempts=settings.outbox_max_attempts,
    )
    if report.claimed:
        logger.info("outbox relay", extra=vars(report))
    return vars(report)


@celery_app.task(
    name="agentflow.execute_task",
    soft_time_limit=EXECUTION_SOFT_TIME_LIMIT,
    time_limit=EXECUTION_TIME_LIMIT,
)
def execute_task(
    event_id: str,
    workspace_id: str,
    workflow_id: str,
    task_id: str,
    agent_version_id: str,
    attempt_count: int = 0,
) -> str:
    """Runs one dispatched task attempt. Idempotent on `event_id` (at-least-once delivery)."""
    factory = get_worker_session_factory()
    with session_scope(factory) as session:
        claimed = runner.claim(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            task_id=uuid.UUID(task_id),
            agent_version_id=uuid.UUID(agent_version_id),
        )
    if isinstance(claimed, runner.Skipped):
        logger.info("dispatch skipped", extra={"event_id": event_id, "outcome": claimed.value})
        return claimed.value

    result: str | None = None
    failure: runner.Failure | None = None
    try:
        result = runner.run_mock_agent(claimed)
    except SoftTimeLimitExceeded:
        failure = runner.Failure("EXECUTION_TIMED_OUT", "The agent exceeded its time limit.", True)
    except Exception as exc:  # the agent failed; record it instead of losing the attempt
        failure = runner.Failure("EXECUTION_FAILED", f"{type(exc).__name__}")

    # Bounded retry of the short final transaction on transient database errors.
    for attempt in range(1, _FINISH_ATTEMPTS + 1):
        try:
            with session_scope(factory) as session:
                outcome = runner.finish(session, claimed, result=result, failure=failure)
            break
        except OperationalError:
            if attempt == _FINISH_ATTEMPTS:
                raise
            time.sleep(0.5 * 2**attempt)
    logger.info(
        "task attempt finished",
        extra={"event_id": event_id, "execution_id": str(claimed.execution_id), "outcome": outcome},
    )
    return outcome


@celery_app.task(
    name="agentflow.analyze_workflow",
    soft_time_limit=ANALYSIS_SOFT_TIME_LIMIT,
    time_limit=ANALYSIS_TIME_LIMIT,
)
def analyze_workflow(event_id: str, workspace_id: str, workflow_id: str) -> str:
    """Analyses a workflow's prompt (Step 2.2). Idempotent on `event_id`."""
    factory = get_worker_session_factory()
    with session_scope(factory) as session:
        job = analysis.claim(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
        )
    if isinstance(job, analysis.Skipped):
        logger.info("analysis skipped", extra={"event_id": event_id, "outcome": job.value})
        return job.value
    outcome: analysis.PromptAnalysis | analysis.AnalysisFailure
    try:
        outcome = analysis.analyse(get_model_gateway(), job)
    except SoftTimeLimitExceeded:
        # Recorded as a failure so the analysis never stays PENDING; the user can retry.
        outcome = analysis.AnalysisFailure(code="ANALYSIS_TIMED_OUT", attempts=0)
    with session_scope(factory) as session:
        return analysis.finish(session, job, outcome)


@celery_app.task(
    name="agentflow.plan_workflow",
    soft_time_limit=ANALYSIS_SOFT_TIME_LIMIT,
    time_limit=ANALYSIS_TIME_LIMIT,
)
def plan_workflow(event_id: str, workspace_id: str, workflow_id: str) -> str:
    """Drafts a plan from the workflow's analysis (Step 2.3). Idempotent on `event_id`."""
    factory = get_worker_session_factory()
    with session_scope(factory) as session:
        job = plan.claim(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
        )
    if isinstance(job, plan.Skipped):
        logger.info("plan generation skipped", extra={"event_id": event_id, "outcome": job.value})
        return job.value
    outcome: plan.PlanProposal | plan.PlanFailure
    try:
        outcome = plan.generate(get_model_gateway(), job)
    except SoftTimeLimitExceeded:
        outcome = plan.PlanFailure(code="PLAN_GENERATION_TIMED_OUT", attempts=0)
    with session_scope(factory) as session:
        return plan.finish(session, job, outcome)


@celery_app.task(name="agentflow.cleanup", ignore_result=True)
def run_cleanup() -> dict[str, int]:
    return cleanup.run_cleanup(
        get_worker_session_factory(),
        batch_size=settings.cleanup_batch_size,
        max_batches=settings.cleanup_max_batches,
    )
