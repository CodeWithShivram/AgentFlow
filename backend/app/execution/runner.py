"""Task execution for Celery workers (ADR-0010, ADR-0018, ADR-0021).

An execution is three bounded steps; no transaction stays open while the agent works:

1. `claim`  - one transaction: record the event in the inbox (a redelivered or duplicate event
              stops here), check the task is still ASSIGNED to the dispatched agent version and
              the workflow is RUNNING with a free slot, then ASSIGNED -> QUEUED -> IN_PROGRESS
              and insert an `agent_executions` row for this attempt.
2. `run_agent` - outside any transaction: build the task contract, run the version's agent
              runtime (model calls through the gateway) and validate its submission (invalid
              output, a failed model call or a version without a runtime fail the attempt).
              Before each model or tool call, an `AttemptGuard` checks the attempt is still
              current, so a cancelled (or recovered) attempt stops at its next call (ADR-0029).
3. `finish` - one transaction: IN_PROGRESS -> SUBMITTED with the submission stored on the
              attempt (agents never complete tasks; platform verification does), or, on
              failure/timeout, REWORK_REQUIRED (FAILED after the last allowed attempt). The
              execution row records the outcome; earlier attempts are kept.
"""

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, BaseAgent
from app.agents.catalog import ToolRisk, highest_tool_risk
from app.agents.contract import (
    AgentSubmission,
    InvalidSubmissionError,
    RetryPolicy,
    TaskContract,
)
from app.agents.specialists import runtime_for
from app.core.config import get_settings
from app.db.sync_session import get_worker_session_factory, session_scope
from app.execution import outbox
from app.llm.gateway import ModelCaller, get_model_gateway
from app.llm.types import CallContext, ModelRequest, ModelResponse
from app.models import (
    AgentExecution,
    AgentVersion,
    ExecutionStatus,
    InterventionSubject,
    InterventionType,
    TaskDependency,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
    WorkspaceRole,
)
from app.projects.context import dependency_files, fix_context
from app.projects.workspace import ProjectWorkspaces
from app.schemas.agents import AgentConfiguration
from app.services.usage import record_tool_invocation
from app.tools.gateway import InvocationRecord, InvocationRecorder, ToolGateway
from app.tools.registry import REGISTRY, ToolCallContext, ToolDefinition, usable_grants
from app.verification import rework
from app.workflows import engine
from app.workflows.state_machine import MAX_ATTEMPTS_PER_TASK as MAX_ATTEMPTS_PER_TASK
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.execution")

CONSUMER = "execute_task"
# The worker's soft time limit for one attempt (handoff section 6 default). A contract never
# promises an agent more time than the worker enforces.
AGENT_TIME_LIMIT_SECONDS = 600
# What a decision on an agent's approval request leads to (ADR-0035), shown with the request.
APPROVED_NEXT = "A new attempt by the same agent, told the action is approved."
REJECTED_NEXT = (
    "A new attempt by the same agent, told to finish without that action; or, if the admin "
    "chooses, the task stops for people to reassign, cancel or replan."
)
IF_EXPIRED = "Handled as a rejection: the agent finishes without that action."
# An agent's approval request expires undecided after this long by default (ADR-0037).
DEFAULT_APPROVAL_TTL = timedelta(hours=24)
# The worker kills an attempt after this long: the soft limit plus time to record the outcome.
AGENT_HARD_TIME_LIMIT_SECONDS = AGENT_TIME_LIMIT_SECONDS + 60


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
    # From the previous attempt's failure (ADR-0032); empty on a first attempt.
    rework_instructions: list[str] = field(default_factory=list)
    # Approved after the previous attempt asked (ADR-0035).
    approved_actions: list[str] = field(default_factory=list)
    # Software project tasks (ADR-0049).
    file_scope: list[str] = field(default_factory=list)
    stack_profile: str | None = None
    dependency_files: list[dict[str, Any]] = field(default_factory=list)
    # Integration fix tasks (ADR-0056).
    integration_feedback: list[dict[str, Any]] = field(default_factory=list)
    current_files: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Failure:
    code: str
    summary: str
    timed_out: bool = False


class AttemptCancelledError(Exception):
    """The attempt is no longer current: its task was cancelled, or the attempt was recovered
    as lost (ADR-0029). The agent stops; `finish` records the attempt as it finds it."""

    code = "ATTEMPT_CANCELLED"


class AttemptGuard:
    """Checks before each model or tool call that the attempt is still current. Once it has
    stopped the attempt it stays stopped, and the runner raises even if the agent caught it."""

    def __init__(self, is_current: Callable[[], bool]) -> None:
        self._is_current = is_current
        self.cancelled = False

    def check(self) -> None:
        if not self.cancelled and not self._is_current():
            self.cancelled = True
        if self.cancelled:
            raise AttemptCancelledError()


class _GuardedModels:
    """The model gateway as one attempt sees it: every call is checked by the guard first."""

    def __init__(self, models: ModelCaller, guard: AttemptGuard) -> None:
        self._models = models
        self._guard = guard

    def complete[T: BaseModel](
        self,
        context: CallContext,
        request: ModelRequest,
        response_format: type[T] | None = None,
    ) -> ModelResponse[T]:
        self._guard.check()
        return self._models.complete(context, request, response_format)


def attempt_is_current(session: Session, workspace_id: uuid.UUID, execution_id: uuid.UUID) -> bool:
    """True while the attempt is RUNNING and its task IN_PROGRESS. Pausing keeps both, so a
    paused workflow lets running attempts finish."""
    row = session.execute(
        select(AgentExecution.status, WorkflowTask.status)
        .join(
            WorkflowTask,
            (WorkflowTask.id == AgentExecution.task_id)
            & (WorkflowTask.workflow_id == AgentExecution.workflow_id),
        )
        .where(AgentExecution.id == execution_id, AgentExecution.workspace_id == workspace_id)
    ).one_or_none()
    return (
        row is not None and row[0] == ExecutionStatus.RUNNING and row[1] == TaskStatus.IN_PROGRESS
    )


def _check_attempt(claim: Claim) -> bool:
    """The guard's default check: one short read. A database error lets the attempt go on;
    `finish` decides with the committed state."""
    try:
        with session_scope(get_worker_session_factory()) as session:
            return attempt_is_current(session, claim.workspace_id, claim.execution_id)
    except SQLAlchemyError as exc:
        logger.warning(
            "attempt check failed",
            extra={"execution_id": str(claim.execution_id), "error_type": type(exc).__name__},
        )
        return True


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
    instructions = rework.previous_attempt_instructions(session, task, attempt_number)
    approved, rejected = rework.previous_attempt_approvals(session, task, attempt_number)
    instructions = [*rejected, *instructions]
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
        rework_instructions=instructions,
    )
    session.add(execution)
    dependencies = session.execute(
        select(TaskDependency.depends_on_task_id)
        .where(TaskDependency.workflow_id == workflow.id, TaskDependency.task_id == task.id)
        .order_by(TaskDependency.depends_on_task_id)
    ).scalars()
    dependency_ids = list(dependencies)
    project_files = dependency_files(session, workflow, dependency_ids) if task.file_scope else []
    feedback, current_files = fix_context(
        session, workflow, task, ProjectWorkspaces(get_settings().project_workspace_root)
    )
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
        dependencies=dependency_ids,
        rework_instructions=instructions,
        approved_actions=approved,
        file_scope=list(task.file_scope),
        stack_profile=(workflow.project_spec or {}).get("stack_profile"),
        dependency_files=project_files,
        integration_feedback=feedback,
        current_files=current_files,
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
            "rework_instructions": claim.rework_instructions,
            "approved_actions": claim.approved_actions,
            "file_scope": claim.file_scope,
            "stack_profile": claim.stack_profile,
            "dependency_files": claim.dependency_files,
            "integration_feedback": claim.integration_feedback,
            "current_files": claim.current_files,
        }
    )


def _record_tool_call(record: InvocationRecord) -> uuid.UUID:
    return record_tool_invocation(get_worker_session_factory(), record)


def run_agent(
    claim: Claim,
    agent_type: type[BaseAgent] | None = None,
    *,
    models: ModelCaller | None = None,
    registry: Mapping[str, ToolDefinition] = REGISTRY,
    record_tool_call: InvocationRecorder = _record_tool_call,
    is_current: Callable[[], bool] | None = None,
) -> AgentSubmission:
    """Runs one attempt outside any transaction, with the runtime chosen from the version's
    instruction reference (ADR-0021); tests may pass `agent_type` and `models` explicitly.
    `is_current` defaults to a database check of the attempt (ADR-0029).

    Raises `AgentRuntimeUnavailableError` for a version without a runtime, `InvalidSubmissionError`
    when the output fails validation, a `ModelGatewayError` when the model call fails, and the
    attempt's first tool policy violation (a `ToolError`) even if the agent caught it (ADR-0019),
    and `AttemptCancelledError` once the attempt stopped being current, even if caught."""
    configuration = AgentConfiguration.model_validate(claim.configuration)
    contract = build_contract(claim, configuration, registry)
    agent = (agent_type or runtime_for(configuration))(configuration)
    guard = AttemptGuard(is_current or partial(_check_attempt, claim))
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
        before_call=guard.check,
    )
    context = AgentContext(
        execution_id=claim.execution_id,
        dispatch_event_id=claim.dispatch_event_id,
        agent_id=claim.agent_id,
        agent_version_id=claim.agent_version_id,
        agent_version=claim.agent_version,
        tools=tools,
        models=_GuardedModels(models or get_model_gateway(), guard),
    )
    try:
        result = agent.execute(contract, context)
    except Exception:
        if guard.cancelled:
            raise AttemptCancelledError() from None
        if tools.violation is not None:
            raise tools.violation from None
        raise
    if guard.cancelled:
        raise AttemptCancelledError()
    if tools.violation is not None:
        raise tools.violation
    validation = agent.validate_output(contract, result)
    if validation.submission is None or validation.issues:
        raise InvalidSubmissionError(validation.issues)
    return validation.submission


def approval_role(configuration: Mapping[str, Any]) -> WorkspaceRole:
    """Who may decide this agent's approval requests (ADR-0038): OWNER when its version is
    granted any HIGH-risk tool (granting those already needs OWNER, ADR-0008), else ADMIN.
    Derived from the platform's own record of the version, never from the agent's request."""
    tools = AgentConfiguration.model_validate(configuration).tool_names()
    return WorkspaceRole.OWNER if highest_tool_risk(tools) == ToolRisk.HIGH else WorkspaceRole.ADMIN


def finish(
    session: Session,
    claim: Claim,
    outcome: AgentSubmission | Failure,
    *,
    approval_ttl: timedelta = DEFAULT_APPROVAL_TTL,
) -> str:
    """Records the attempt's outcome. An agent's approval request expires after `approval_ttl`
    (the worker passes `Settings.intervention_approval_ttl_hours`, ADR-0037)."""
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
    if isinstance(outcome, AgentSubmission) and outcome.approval_request is not None:
        # The agent asks people first (ADR-0035): no verification until a decision.
        execution.status = ExecutionStatus.SUCCEEDED
        execution.submission = outcome.model_dump(mode="json")
        execution.result_summary = outcome.summary
        engine.transition_task(
            session,
            workflow,
            task,
            TaskStatus.WAITING_APPROVAL,
            actor=Actor.WORKER,
            extra=context,
        )
        request = outcome.approval_request
        engine.open_intervention(
            session,
            workflow,
            type=InterventionType.APPROVAL,
            subject=InterventionSubject.AGENT_ACTION,
            requested_action=request.action,
            context={
                "reason": request.reason,
                "risks": list(request.risks),
                "consequences": list(request.consequences),
                "execution_id": str(execution.id),
                "attempt_number": claim.attempt_number,
                "if_approved": APPROVED_NEXT,
                "if_rejected": REJECTED_NEXT,
                "if_expired": IF_EXPIRED,
            },
            source_key=f"agent_action:{execution.id}",
            task_id=task.id,
            expires_at=datetime.now(UTC) + approval_ttl,
            required_role=approval_role(claim.configuration),
            requested_by="AGENT",
            requested_by_agent_id=claim.agent_id,
        )
        # Workflows whose plan needs approval also stop for the decision (ADR-0036).
        engine.pause_for_decision(session, workflow)
        result = "waiting_approval"
    elif isinstance(outcome, AgentSubmission):
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
            else TaskStatus.ESCALATED  # attempts exhausted: people decide (ADR-0034)
        )
        engine.transition_task(
            session, workflow, task, target, actor=Actor.SYSTEM, reason=outcome.code, extra=context
        )
        result = target.value.lower()
        if target == TaskStatus.REWORK_REQUIRED:  # the next attempt, if the policy allows (5.3)
            rework.schedule_rework(session, workflow, task, failure_codes=[outcome.code])
    # The attempt's model calls are recorded (and costed) by now: stop at the budget (ADR-0040).
    engine.pause_at_budget(session, workflow)
    session.commit()
    return result
