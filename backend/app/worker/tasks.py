"""Celery tasks. Thin wrappers: the logic lives in app.execution (runner, relay, cleanup)."""

import logging
import os
import time
import uuid
from datetime import timedelta
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.agents.contract import AgentSubmission, InvalidSubmissionError
from app.agents.specialists import AgentRuntimeUnavailableError
from app.artifacts.records import storage_from_settings
from app.db.sync_session import get_worker_session_factory, session_scope
from app.execution import cleanup, heartbeat, outbox, recovery, relay, runner
from app.llm.gateway import ModelGatewayError, get_model_gateway
from app.orchestration import job as orchestration_job
from app.planning import analysis, plan
from app.projects import apply_job, integration_job, package_job, purge_job
from app.projects.workspace import ProjectWorkspaces
from app.sandbox.runner import DockerSandbox
from app.tools.gateway import ToolError
from app.verification import job as verification_job
from app.verification import semantic
from app.worker.celery_app import EXECUTION_QUEUE, celery_app, settings

logger = logging.getLogger("agentflow.worker")

# Outbox event type -> (Celery task name, queue).
EVENT_ROUTES = {
    outbox.TASK_DISPATCH: ("agentflow.execute_task", EXECUTION_QUEUE),
    outbox.TASK_VERIFY: ("agentflow.verify_task", EXECUTION_QUEUE),
    outbox.TASK_EVALUATE: ("agentflow.evaluate_task", EXECUTION_QUEUE),
    outbox.WORKFLOW_ANALYZE: ("agentflow.analyze_workflow", EXECUTION_QUEUE),
    outbox.WORKFLOW_PLAN: ("agentflow.plan_workflow", EXECUTION_QUEUE),
    outbox.WORKFLOW_ORCHESTRATE: ("agentflow.orchestrate_workflow", EXECUTION_QUEUE),
    outbox.PROJECT_APPLY: ("agentflow.apply_to_workspace", EXECUTION_QUEUE),
    outbox.PROJECT_INTEGRATE: ("agentflow.integrate_project", EXECUTION_QUEUE),
    outbox.PROJECT_PACKAGE: ("agentflow.package_project", EXECUTION_QUEUE),
    outbox.WORKFLOW_PURGE: ("agentflow.purge_workflow", EXECUTION_QUEUE),
}
# The identifiers each event's task needs; a payload missing one can never be consumed.
_WORKFLOW_IDS = frozenset({"workspace_id", "workflow_id"})
REQUIRED_PAYLOAD = {
    outbox.TASK_DISPATCH: _WORKFLOW_IDS | {"task_id", "agent_version_id"},
    outbox.TASK_VERIFY: _WORKFLOW_IDS | {"task_id"},
    outbox.TASK_EVALUATE: _WORKFLOW_IDS | {"task_id"},
    outbox.WORKFLOW_ANALYZE: _WORKFLOW_IDS,
    outbox.WORKFLOW_PLAN: _WORKFLOW_IDS,
    outbox.WORKFLOW_ORCHESTRATE: _WORKFLOW_IDS,
    outbox.PROJECT_APPLY: _WORKFLOW_IDS | {"task_id"},
    outbox.PROJECT_INTEGRATE: _WORKFLOW_IDS,
    outbox.PROJECT_PACKAGE: _WORKFLOW_IDS,
    outbox.WORKFLOW_PURGE: _WORKFLOW_IDS,
}
_FINISH_ATTEMPTS = 3
# Default agent timeout (handoff section 6): 600 s. Hard limit gives the soft one room to finish.
EXECUTION_SOFT_TIME_LIMIT = runner.AGENT_TIME_LIMIT_SECONDS
EXECUTION_TIME_LIMIT = runner.AGENT_HARD_TIME_LIMIT_SECONDS
# Prompt analysis and plan generation: three model attempts with short backoff.
ANALYSIS_SOFT_TIME_LIMIT = analysis.JOB_SOFT_TIME_LIMIT_SECONDS
ANALYSIS_TIME_LIMIT = analysis.JOB_TIME_LIMIT_SECONDS


def publish_event(event: outbox.ClaimedEvent) -> None:
    """Hands one outbox event to the broker. Raises if the broker does not accept it."""
    route = EVENT_ROUTES.get(event.event_type)
    if route is None:
        raise outbox.PermanentPublishError(f"no route for outbox event type {event.event_type!r}")
    missing = REQUIRED_PAYLOAD[event.event_type] - set(event.payload)
    if missing:
        raise outbox.PermanentPublishError(f"payload lacks {', '.join(sorted(missing))}")
    task_name, queue = route
    celery_app.send_task(
        task_name,
        kwargs={**event.payload, "event_id": str(event.id)},
        task_id=str(event.id),  # the stable event ID doubles as the message ID
        queue=queue,
        retry=False,  # failures are recorded on the outbox row and retried by the relay
    )


_heartbeat_clients: dict[int, Redis] = {}


def _heartbeat_client() -> Redis:
    """A Redis client per worker process (created lazily, like the database engine)."""
    pid = os.getpid()
    if pid not in _heartbeat_clients:
        _heartbeat_clients.clear()
        _heartbeat_clients[pid] = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password.get_secret_value(),
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
    return _heartbeat_clients[pid]


def _record_heartbeat(factory: Any) -> None:
    """Readiness reads this (ADR-0027). A failure here never stops the relay."""
    try:
        with session_scope(factory) as session:
            oldest_due = heartbeat.oldest_due_seconds(session)
        _heartbeat_client().set(
            heartbeat.KEY,
            heartbeat.encode(at=time.time(), oldest_due=oldest_due),
            ex=heartbeat.TTL_SECONDS,
        )
    except (RedisError, OSError, SQLAlchemyError) as exc:
        logger.warning("relay heartbeat failed", extra={"error_type": type(exc).__name__})


@celery_app.task(name="agentflow.relay_outbox", ignore_result=True)
def relay_outbox() -> dict[str, int]:
    factory = get_worker_session_factory()
    report = relay.relay_once(
        factory,
        publish_event,
        batch_size=settings.outbox_batch_size,
        lease_seconds=settings.outbox_lease_seconds,
        max_attempts=settings.outbox_max_attempts,
    )
    if report.claimed:
        logger.info("outbox relay", extra=vars(report))
    _record_heartbeat(factory)
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

    result: AgentSubmission | runner.Failure
    try:
        result = runner.run_agent(claimed)
    except SoftTimeLimitExceeded:
        result = runner.Failure("EXECUTION_TIMED_OUT", "The agent exceeded its time limit.", True)
    except InvalidSubmissionError as exc:  # only issue codes; never the agent's output
        result = runner.Failure("INVALID_SUBMISSION", f"Invalid submission: {exc}")
    except AgentRuntimeUnavailableError as exc:
        result = runner.Failure(exc.code, exc.message)
    except ModelGatewayError as exc:  # recorded in llm_usage; never provider text
        result = runner.Failure(exc.code, "The model call failed.")
    except runner.AttemptCancelledError as exc:  # cancelled or recovered: finish records it
        result = runner.Failure(
            exc.code, "The attempt was stopped because it is no longer current."
        )
    except ToolError as exc:  # a tool policy violation, or a tool error the agent did not handle
        result = runner.Failure(exc.code, exc.message)
    except Exception as exc:  # the agent failed; record it instead of losing the attempt
        result = runner.Failure("EXECUTION_FAILED", f"{type(exc).__name__}")

    # Bounded retry of the short final transaction on transient database errors.
    for attempt in range(1, _FINISH_ATTEMPTS + 1):
        try:
            with session_scope(factory) as session:
                outcome = runner.finish(
                    session,
                    claimed,
                    result,
                    approval_ttl=timedelta(hours=settings.intervention_approval_ttl_hours),
                )
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


@celery_app.task(name="agentflow.verify_task", soft_time_limit=30, time_limit=60)
def verify_task(event_id: str, workspace_id: str, workflow_id: str, task_id: str) -> str:
    """Verifies a submitted task (ADR-0030). Idempotent on `event_id`; no model call, one short
    transaction."""
    with session_scope(get_worker_session_factory()) as session:
        outcome = verification_job.run(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            task_id=uuid.UUID(task_id),
        )
    logger.info("task verification", extra={"event_id": event_id, "outcome": outcome})
    return outcome


@celery_app.task(
    name="agentflow.evaluate_task",
    soft_time_limit=semantic.JOB_SOFT_TIME_LIMIT_SECONDS,
    time_limit=semantic.JOB_TIME_LIMIT_SECONDS,
)
def evaluate_task(event_id: str, workspace_id: str, workflow_id: str, task_id: str) -> str:
    """Semantic evaluation of a submission (ADR-0031). Idempotent on `event_id`; the model is
    called outside any transaction."""
    factory = get_worker_session_factory()
    with session_scope(factory) as session:
        job = semantic.claim(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            task_id=uuid.UUID(task_id),
        )
    if isinstance(job, semantic.Skipped):
        logger.info("evaluation skipped", extra={"event_id": event_id, "outcome": job.value})
        return job.value
    outcome: semantic.Evaluation | semantic.EvaluationFailure
    try:
        outcome = semantic.evaluate(get_model_gateway(), job)
    except SoftTimeLimitExceeded:
        outcome = semantic.EvaluationFailure(code="EVALUATION_TIMED_OUT", attempts=0)
    with session_scope(factory) as session:
        result = semantic.finish(session, job, outcome)
    logger.info("task evaluation", extra={"event_id": event_id, "outcome": result})
    return result


@celery_app.task(name="agentflow.orchestrate_workflow", soft_time_limit=30, time_limit=60)
def orchestrate_workflow(event_id: str, workspace_id: str, workflow_id: str) -> str:
    """Reassesses a workflow with the orchestration graph (ADR-0025). Idempotent on
    `event_id`; no model call, one short transaction."""
    with session_scope(get_worker_session_factory()) as session:
        outcome = orchestration_job.run(
            session,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
        )
    logger.info("workflow assessed", extra={"event_id": event_id, "outcome": outcome.value})
    return outcome.value


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
            workspaces=ProjectWorkspaces(settings.project_workspace_root),
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


@celery_app.task(name="agentflow.recover", ignore_result=True)
def recover() -> dict[str, int]:
    """Recovers work stranded by a lost worker or message (ADR-0029)."""
    report = recovery.recover_once(get_worker_session_factory())
    if any(vars(report).values()):
        logger.info("recovery sweep", extra=vars(report))
    return vars(report)


@celery_app.task(name="agentflow.cleanup", ignore_result=True)
def run_cleanup() -> dict[str, int]:
    return cleanup.run_cleanup(
        get_worker_session_factory(),
        batch_size=settings.cleanup_batch_size,
        max_batches=settings.cleanup_max_batches,
    )


@celery_app.task(name="agentflow.apply_to_workspace", soft_time_limit=120, time_limit=180)
def apply_to_workspace(event_id: str, workspace_id: str, workflow_id: str, task_id: str) -> str:
    """Applies an accepted software project task to its workspace (ADR-0049). Idempotent on
    `event_id` and per attempt; no model call."""
    workspaces = ProjectWorkspaces(settings.project_workspace_root)
    with session_scope(get_worker_session_factory()) as session:
        outcome = apply_job.run(
            session,
            workspaces,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            task_id=uuid.UUID(task_id),
        )
    logger.info("workspace apply", extra={"event_id": event_id, "outcome": outcome})
    return outcome


@celery_app.task(name="agentflow.integrate_project", soft_time_limit=900, time_limit=960)
def integrate_project(event_id: str, workspace_id: str, workflow_id: str) -> str:
    """Runs a software project's integration gate (ADR-0051): claim, sandbox checks outside any
    transaction, finish. Idempotent on `event_id`; at most one run per project at a time."""
    workspaces = ProjectWorkspaces(settings.project_workspace_root)
    factory = get_worker_session_factory()
    with session_scope(factory) as session:
        job = integration_job.claim(
            session,
            workspaces,
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
        )
    if isinstance(job, str):
        logger.info("integration skipped", extra={"event_id": event_id, "outcome": job})
        return job
    outcome = integration_job.execute(DockerSandbox(), job)
    with session_scope(factory) as session:
        return integration_job.finish(
            session, workspaces, job, outcome, storage_from_settings(settings)
        )


@celery_app.task(name="agentflow.package_project", soft_time_limit=300, time_limit=360)
def package_project(
    event_id: str,
    workspace_id: str,
    workflow_id: str,
    partial: bool = False,
    requested_by: str | None = None,
) -> str:
    """Packages a software project's deliverable (ADR-0052). Idempotent on `event_id`."""
    with session_scope(get_worker_session_factory()) as session:
        outcome = package_job.run(
            session,
            ProjectWorkspaces(settings.project_workspace_root),
            storage_from_settings(settings),
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
            partial=partial,
            requested_by=uuid.UUID(requested_by) if requested_by else None,
        )
    logger.info("project packaging", extra={"event_id": event_id, "outcome": outcome})
    return outcome


@celery_app.task(name="agentflow.purge_workflow", soft_time_limit=120, time_limit=180)
def purge_workflow(event_id: str, workspace_id: str, workflow_id: str) -> str:
    """Removes a deleted workflow's workspace and stored files (ADR-0058). Idempotent."""
    with session_scope(get_worker_session_factory()) as session:
        outcome = purge_job.run(
            session,
            ProjectWorkspaces(settings.project_workspace_root),
            storage_from_settings(settings),
            event_id=uuid.UUID(event_id),
            workspace_id=uuid.UUID(workspace_id),
            workflow_id=uuid.UUID(workflow_id),
        )
    logger.info("workflow purge", extra={"event_id": event_id, "outcome": outcome})
    return outcome
