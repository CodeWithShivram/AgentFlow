"""Task execution for Celery workers (ADR-0010, ADR-0018, ADR-0021).

An execution is three bounded steps; no transaction stays open while the agent works:

1. `claim`  - one transaction: record the event in the inbox (a redelivered or duplicate event
              stops here), check the task is still ASSIGNED to the dispatched agent version and
              the workflow is RUNNING with a free slot, then ASSIGNED -> QUEUED -> IN_PROGRESS
              and insert an `agent_executions` row for this attempt.
2. `run_agent` - outside any transaction: build the task contract, run the version's agent
              runtime (model calls through the gateway) and validate its submission (invalid
              output, a failed model call or a version without a runtime fail the attempt).
3. `finish` - one transaction: IN_PROGRESS -> SUBMITTED with the submission stored on the
              attempt (agents never complete tasks; platform verification does), or, on
              failure/timeout, REWORK_REQUIRED (FAILED after the last allowed attempt). The
              execution row records the outcome; earlier attempts are kept.
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, BaseAgent
from app.agents.contract import (
    AgentSubmission,
    InvalidSubmissionError,
    RetryPolicy,
    TaskContract,
)
from app.agents.specialists import runtime_for
from app.db.sync_session import get_worker_session_factory
from app.execution import outbox
from app.llm.gateway import ModelGateway, get_model_gateway
from app.models import (
    AgentExecution,
    AgentVersion,
    ExecutionStatus,
    TaskDependency,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.schemas.agents import AgentConfiguration
from app.services.usage import record_tool_invocation
from app.tools.gateway import InvocationRecord, InvocationRecorder, ToolGateway
from app.tools.registry import REGISTRY, ToolCallContext, ToolDefinition, usable_grants
from app.workflows import engine
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.execution")

CONSUMER = "execute_task"
# Handoff section 6: at most 3 attempts per task.
MAX_ATTEMPTS_PER_TASK = 3
# The worker's soft time limit for one attempt (handoff section 6 default). A contract never
# promises an agent more time than the worker enforces.
AGENT_TIME_LIMIT_SECONDS = 600


class Skipped(StrEnum):
    DUPLICATE = "duplicate"  # this event was already consumed
    STALE = "stale"  # the task moved on, or was reassigned to another version
    NOT_RUNNING = "not_running"  # workflow paused/blocked/finished; resume re-dispatches
    DEFERRED = "deferred"  # no free slot; a finishing task re-dispatches it


@dataclass(frozen=True)
class Claim:
    """A claimed attempt, with a snapshot of the data its task contract is built from."""

    execution_id: uuid.UUID
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    attempt_number: int
    dispatch_event_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    agent_version: int
    configuration: dict[str, Any]
    title: str
    description: str
    input_references: list[str]
    required_skills: list[str]
    deliverables: list[str]
    output_schema: dict[str, Any] | None
    acceptance_criteria: list[dict[str, Any]]
    dependencies: list[uuid.UUID]


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
    dependencies = session.execute(
        select(TaskDependency.depends_on_task_id)
        .where(TaskDependency.workflow_id == workflow.id, TaskDependency.task_id == task.id)
        .order_by(TaskDependency.depends_on_task_id)
    ).scalars()
    claimed = Claim(
        execution_id=execution.id,
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        attempt_number=attempt_number,
        dispatch_event_id=event_id,
        agent_id=task.assigned_agent_id,
        agent_version_id=agent_version_id,
        agent_version=version.version,
        configuration=version.configuration,
        title=task.title,
        description=task.description,
        input_references=list(task.input_references),
        required_skills=list(task.required_skills),
        deliverables=list(task.deliverables),
        output_schema=task.expected_output_schema,
        acceptance_criteria=list(task.acceptance_criteria),
        dependencies=list(dependencies),
    )
    session.commit()
    return claimed


def build_contract(
    claim: Claim,
    configuration: AgentConfiguration,
    registry: Mapping[str, ToolDefinition] = REGISTRY,
) -> TaskContract:
    """The contract for this attempt (handoff section 5.3), built only from platform data."""
    return TaskContract.model_validate(
        {
            "workspace_id": claim.workspace_id,
            "workflow_id": claim.workflow_id,
            "task_id": claim.task_id,
            "objective": claim.title,
            "context": claim.description,
            "input_references": claim.input_references,
            "required_skills": claim.required_skills,
            "deliverables": claim.deliverables,
            "output_schema": claim.output_schema,
            "acceptance_criteria": claim.acceptance_criteria,
            "dependencies": claim.dependencies,
            "allowed_tools": usable_grants(configuration, registry),
            "timeout_seconds": min(configuration.limits.timeout_seconds, AGENT_TIME_LIMIT_SECONDS),
            "token_budget": configuration.limits.max_tokens_per_task,
            "retry_policy": RetryPolicy(
                max_attempts=MAX_ATTEMPTS_PER_TASK, attempt_number=claim.attempt_number
            ),
        }
    )


def _record_tool_call(record: InvocationRecord) -> uuid.UUID:
    return record_tool_invocation(get_worker_session_factory(), record)


def run_agent(
    claim: Claim,
    agent_type: type[BaseAgent] | None = None,
    *,
    models: ModelGateway | None = None,
    registry: Mapping[str, ToolDefinition] = REGISTRY,
    record_tool_call: InvocationRecorder = _record_tool_call,
) -> AgentSubmission:
    """Runs one attempt outside any transaction, with the runtime chosen from the version's
    instruction reference (ADR-0021); tests may pass `agent_type` and `models` explicitly.

    Raises `AgentRuntimeUnavailableError` for a version without a runtime, `InvalidSubmissionError`
    when the output fails validation, a `ModelGatewayError` when the model call fails, and the
    attempt's first tool policy violation (a `ToolError`) even if the agent caught it (ADR-0019)."""
    configuration = AgentConfiguration.model_validate(claim.configuration)
    contract = build_contract(claim, configuration, registry)
    agent = (agent_type or runtime_for(configuration))(configuration)
    tools = ToolGateway(
        ToolCallContext(
            workspace_id=claim.workspace_id,
            workflow_id=claim.workflow_id,
            task_id=claim.task_id,
            execution_id=claim.execution_id,
        ),
        contract.allowed_tools,
        record_tool_call,
        registry=registry,
    )
    context = AgentContext(
        execution_id=claim.execution_id,
        dispatch_event_id=claim.dispatch_event_id,
        agent_id=claim.agent_id,
        agent_version_id=claim.agent_version_id,
        agent_version=claim.agent_version,
        tools=tools,
        models=models or get_model_gateway(),
    )
    try:
        result = agent.execute(contract, context)
    except Exception:
        if tools.violation is not None:
            raise tools.violation from None
        raise
    if tools.violation is not None:
        raise tools.violation
    validation = agent.validate_output(contract, result)
    if validation.submission is None or validation.issues:
        raise InvalidSubmissionError(validation.issues)
    return validation.submission


def finish(session: Session, claim: Claim, outcome: AgentSubmission | Failure) -> str:
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
    if isinstance(outcome, AgentSubmission):
        execution.status = ExecutionStatus.SUCCEEDED
        execution.submission = outcome.model_dump(mode="json")
        execution.result_summary = outcome.summary
        engine.transition_task(
            session, workflow, task, TaskStatus.SUBMITTED, actor=Actor.WORKER, extra=context
        )
        result = "submitted"
    else:
        execution.status = (
            ExecutionStatus.TIMED_OUT if outcome.timed_out else ExecutionStatus.FAILED
        )
        execution.error_code = outcome.code
        execution.error_summary = outcome.summary[:1000]
        target = (
            TaskStatus.REWORK_REQUIRED
            if claim.attempt_number < MAX_ATTEMPTS_PER_TASK
            else TaskStatus.FAILED
        )
        engine.transition_task(
            session, workflow, task, target, actor=Actor.SYSTEM, reason=outcome.code, extra=context
        )
        result = target.value.lower()
    session.commit()
    return result
