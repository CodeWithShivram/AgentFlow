"""Rework instructions, bounded retries and reassignment (Step 5.3, ADR-0032).

After an attempt fails (its execution failed, or verification rejected it), `schedule_rework`
decides what happens next, in the same transaction as the failure and under the workflow lock:

- policy failures (a tool-policy violation, a version without a runtime, a task schema that cannot
  be applied) are escalated to people (ADR-0034): retrying would repeat them;
- otherwise the scheduler starts the next attempt itself (REWORK_REQUIRED -> ASSIGNED), within the
  task's three attempts; the third one goes to another agent once (`MAX_REASSIGNMENTS`) when the
  same agent failed twice and another eligible agent exists (the platform decides and stores
  the selection rationale, ADR-0023 as amended).

`instructions_for` turns the previous attempt's failure into corrective instructions for the next
one: fixed platform sentences per error or issue code. They name criteria by id and deliverables by
position, never quoting user, agent or model text, because they reach the model outside the
delimited contract (`app/agents/specialists.py`). The runner stores them on the new attempt.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.selection import Candidate, CandidateInput, rank_candidates
from app.models import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ApprovalDecision,
    ExecutionStatus,
    HumanIntervention,
    InterventionStatus,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowEventType,
    WorkflowTask,
)
from app.schemas.agents import AgentConfiguration
from app.services import selection as selection_service
from app.workflows import engine
from app.workflows.state_machine import WORKFLOW_EXECUTING, Actor

logger = logging.getLogger("agentflow.verification")

POLICY_VERSION = "retry/v1"
MAX_REASSIGNMENTS = 1
MAX_INSTRUCTIONS = 20
# Failures a retry would only repeat: they wait for people (owner decision 2026-11-15).
NOT_RETRIED = frozenset(
    {
        "TOOL_PERMISSION_DENIED",
        "TOOL_CALL_LIMIT_EXCEEDED",
        "AGENT_RUNTIME_UNAVAILABLE",
        "OUTPUT_SCHEMA_INVALID",
        "OUTPUT_SCHEMA_UNUSABLE",
    }
)
# Failures that are not the agent's: they never count toward a reassignment.
PLATFORM_FAILURES = frozenset({"WORKER_LOST"})

_ISSUE_SENTENCES = {
    "SUBMISSION_MISSING": "Reply with one JSON object that matches the submission schema.",
    "SUBMISSION_INVALID": "Reply with one JSON object that matches the submission schema.",
    "MISSING_CRITERION_CLAIM": (
        "Make exactly one claim for each acceptance criterion in the contract, and no others."
    ),
    "DUPLICATE_CRITERION_CLAIM": (
        "Make exactly one claim for each acceptance criterion in the contract, and no others."
    ),
    "UNKNOWN_CRITERION": (
        "Make exactly one claim for each acceptance criterion in the contract, and no others."
    ),
    "CRITERION_NOT_MET": (
        "Meet acceptance criterion {criterion}: your previous attempt reported it as not met."
    ),
    "CRITERION_JUDGED_NOT_MET": (
        "Meet acceptance criterion {criterion}: the platform's evaluation judged that your "
        "previous attempt did not meet it."
    ),
    "OBJECTIVE_NOT_MET": (
        "Achieve the task's objective: the platform's evaluation judged that your previous "
        "attempt did not."
    ),
    "CONFLICTS_WITH_REQUIREMENTS": (
        "Remove anything that contradicts the task's objective, context or acceptance criteria."
    ),
    "DELIVERABLE_MISSING": (
        "Provide non-empty content for deliverable {deliverable} of the contract in outputs, "
        "keyed by its exact text."
    ),
    "OUTPUT_SCHEMA_MISMATCH": "Make outputs conform to the task's output schema.",
    "OUTPUT_SCHEMA_MORE_MISMATCHES": "Make outputs conform to the task's output schema.",
    "OUTPUTS_INVALID": "Make outputs fit the structure your role requires.",
    # Software project tasks (ADR-0049).
    "FILES_MISSING": (
        "Propose the task's files in outputs.proposed_files (and tests in proposed_tests)."
    ),
    "FILE_OUTSIDE_SCOPE": (
        "Propose only files inside the contract's file_scope; other tasks own the rest."
    ),
    "TEST_CASE_UNKNOWN_CRITERION": (
        "Let test cases cover only the acceptance criteria in the contract."
    ),
}
_ERROR_SENTENCES = {
    "INVALID_SUBMISSION": (
        "Reply with one JSON object that matches the submission schema, with exactly one claim "
        "for each acceptance criterion."
    ),
    "EXECUTION_TIMED_OUT": "Finish within the time limit; keep the work to the deliverables.",
}


def instructions_for(
    *,
    error_code: str | None,
    issues: Sequence[Mapping[str, Any]],
    deliverables: Sequence[str],
) -> list[str]:
    """Corrective instructions for the next attempt, from the previous attempt's error code and
    its verification errors (warnings are left out). Deduplicated, in order, capped."""
    sentences: list[str] = []
    if error_code in _ERROR_SENTENCES:
        sentences.append(_ERROR_SENTENCES[error_code])
    for issue in issues:
        if issue.get("severity") != "error" or issue.get("code") not in _ISSUE_SENTENCES:
            continue
        template = _ISSUE_SENTENCES[str(issue["code"])]
        name = issue.get("deliverable")
        position = deliverables.index(name) + 1 if name in deliverables else None
        if "{deliverable}" in template and position is None:
            continue
        if "{criterion}" in template and not issue.get("criterion_id"):
            continue
        sentences.append(template.format(criterion=issue.get("criterion_id"), deliverable=position))
    return list(dict.fromkeys(sentences))[:MAX_INSTRUCTIONS]


def previous_attempt_instructions(
    session: Session, task: WorkflowTask, attempt_number: int
) -> list[str]:
    """Instructions for `attempt_number`, from the attempt before it (none for the first)."""
    previous = session.execute(
        select(AgentExecution).where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == task.workflow_id,
            AgentExecution.workspace_id == task.workspace_id,
            AgentExecution.attempt_number == attempt_number - 1,
        )
    ).scalar_one_or_none()
    if previous is None:
        return []
    issues = [
        issue
        for result in session.execute(
            select(VerificationResult)
            .where(
                VerificationResult.execution_id == previous.id,
                VerificationResult.workspace_id == task.workspace_id,
            )
            .order_by(VerificationResult.created_at)
        ).scalars()
        for issue in result.issues
    ]
    return instructions_for(
        error_code=previous.error_code, issues=issues, deliverables=list(task.deliverables)
    )


REJECTED_INSTRUCTION = (
    "People rejected the action your previous attempt asked approval for: complete the task "
    "without it."
)


def previous_attempt_approvals(
    session: Session, task: WorkflowTask, attempt_number: int
) -> tuple[list[str], list[str]]:
    """A decision on the previous attempt's approval request (ADR-0035): the approved action
    (agent text, passed as data in the contract), or a fixed instruction after a rejection."""
    previous = session.execute(
        select(AgentExecution.id).where(
            AgentExecution.task_id == task.id,
            AgentExecution.workflow_id == task.workflow_id,
            AgentExecution.workspace_id == task.workspace_id,
            AgentExecution.attempt_number == attempt_number - 1,
        )
    ).scalar_one_or_none()
    if previous is None:
        return [], []
    request = session.execute(
        select(HumanIntervention).where(
            HumanIntervention.workflow_id == task.workflow_id,
            HumanIntervention.workspace_id == task.workspace_id,
            HumanIntervention.source_key == f"agent_action:{previous}",
            # An expired request was handled as a rejection (ADR-0037).
            HumanIntervention.status.in_([InterventionStatus.RESOLVED, InterventionStatus.EXPIRED]),
        )
    ).scalar_one_or_none()
    decision = (request.resolution or {}).get("decision") if request else None
    if decision == "approve" and request is not None:
        # Bound to exactly what was shown (ADR-0038): the audit record must exist and match.
        record = session.execute(
            select(ApprovalDecision).where(
                ApprovalDecision.intervention_id == request.id,
                ApprovalDecision.workspace_id == task.workspace_id,
            )
        ).scalar_one_or_none()
        if (
            record is None
            or record.decision != "approve"
            or record.action_fingerprint != engine.action_fingerprint(request)
        ):
            logger.warning(
                "approval not applied: its audit record is missing or does not match",
                extra={"intervention_id": str(request.id), "task_id": str(task.id)},
            )
            return [], []
        return [request.requested_action], []
    if decision == "reject":
        return [], [REJECTED_INSTRUCTION]
    return [], []


# --- the retry decision ------------------------------------------------------------------------


def _failure_codes(session: Session, execution: AgentExecution) -> set[str]:
    """Why one attempt failed: its error code, or its verification error codes."""
    codes = {execution.error_code} if execution.error_code else set()
    for result in session.execute(
        select(VerificationResult).where(
            VerificationResult.execution_id == execution.id,
            VerificationResult.workspace_id == execution.workspace_id,
            VerificationResult.outcome == VerificationOutcome.FAILED,
        )
    ).scalars():
        codes |= {i["code"] for i in result.issues if i.get("severity") == "error"}
    return codes


def _agent_failed(session: Session, execution: AgentExecution) -> bool:
    if execution.status == ExecutionStatus.CANCELLED:
        return False
    codes = _failure_codes(session, execution)
    return bool(codes) and not codes <= PLATFORM_FAILURES


def _rank(session: Session, workflow: Workflow, task: WorkflowTask) -> list[Candidate]:
    """The selection rules' ranking (ADR-0023), with the same inputs as the API's."""
    rows = session.execute(
        select(Agent, AgentVersion)
        .join(AgentVersion, Agent.active_version_id == AgentVersion.id)
        .where(
            (Agent.workspace_id == workflow.workspace_id) | Agent.workspace_id.is_(None),
            Agent.status == AgentStatus.ACTIVE,
        )
        .order_by(Agent.name, Agent.id)
    ).all()
    busy: dict[uuid.UUID, int] = {}
    for agent_id in session.execute(
        select(WorkflowTask.assigned_agent_id).where(
            WorkflowTask.workspace_id == workflow.workspace_id,
            WorkflowTask.assigned_agent_id.is_not(None),
            WorkflowTask.removed_at.is_(None),
            WorkflowTask.status.in_(
                [TaskStatus.ASSIGNED, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]
            ),
        )
    ).scalars():
        if agent_id is not None:
            busy[agent_id] = busy.get(agent_id, 0) + 1
    candidates = [
        CandidateInput(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_version_id=version.id,
            agent_version=version.version,
            configuration=AgentConfiguration.model_validate(version.configuration),
            in_flight=busy.get(agent.id, 0),
        )
        for agent, version in rows
    ]
    return rank_candidates(list(task.required_skills), candidates)


def schedule_rework(
    session: Session, workflow: Workflow, task: WorkflowTask, *, failure_codes: Sequence[str]
) -> str:
    """Called right after a task moved to REWORK_REQUIRED, in that transaction (ADR-0032).
    Returns "waiting" (the workflow is not executing), "escalated", "retried" or
    "reassigned"."""
    if task.status != TaskStatus.REWORK_REQUIRED or workflow.status not in WORKFLOW_EXECUTING:
        return "waiting"
    if NOT_RETRIED & set(failure_codes):
        # A retry would repeat it: people decide (ADR-0034).
        engine.transition_task(
            session,
            workflow,
            task,
            TaskStatus.ESCALATED,
            actor=Actor.SYSTEM,
            reason="POLICY_FAILURE",
            extra={"failure_codes": sorted(NOT_RETRIED & set(failure_codes))},
        )
        return "escalated"
    attempts = list(
        session.execute(
            select(AgentExecution)
            .where(
                AgentExecution.task_id == task.id,
                AgentExecution.workflow_id == workflow.id,
                AgentExecution.workspace_id == workflow.workspace_id,
            )
            .order_by(AgentExecution.attempt_number)
        ).scalars()
    )
    reassign_to: Candidate | None = None
    ranked: list[Candidate] = []
    reassignments = len({a.agent_id for a in attempts}) - 1
    last_two = attempts[-2:]
    if (
        reassignments < MAX_REASSIGNMENTS
        and len(last_two) == 2
        and all(a.agent_id == task.assigned_agent_id for a in last_two)
        and all(_agent_failed(session, a) for a in last_two)
    ):
        ranked = _rank(session, workflow, task)
        reassign_to = next(
            (c for c in ranked if c.rank is not None and c.agent_id != task.assigned_agent_id),
            None,
        )

    task.attempt_count += 1
    extra: dict[str, Any] = {
        "attempt_count": task.attempt_count,
        "automatic_retry": True,
        "policy": POLICY_VERSION,
    }
    if reassign_to is None:
        engine.transition_task(
            session,
            workflow,
            task,
            TaskStatus.ASSIGNED,
            actor=Actor.SCHEDULER,
            reason="automatic retry",
            extra={
                **extra,
                "agent_id": str(task.assigned_agent_id),
                "agent_version_id": str(task.assigned_agent_version_id),
            },
        )
        return "retried"

    previous = (task.assigned_agent_id, task.assigned_agent_version_id)
    task.assigned_agent_id = reassign_to.agent_id
    task.assigned_agent_version_id = reassign_to.agent_version_id
    selection = selection_service.record_selection(
        session,
        workflow,
        task,
        ranked,
        agent_id=reassign_to.agent_id,
        agent_version_id=reassign_to.agent_version_id,
        user_id=None,  # a platform decision (ADR-0023 as amended by ADR-0032)
    )
    session.flush()
    engine.transition_task(
        session,
        workflow,
        task,
        TaskStatus.ASSIGNED,
        actor=Actor.SCHEDULER,
        reason="automatic reassignment",
        event_type=WorkflowEventType.TASK_REASSIGNED,
        extra={
            **extra,
            "agent_id": str(reassign_to.agent_id),
            "agent_version_id": str(reassign_to.agent_version_id),
            "agent_version": reassign_to.agent_version,
            "previous_agent_id": str(previous[0]),
            "previous_agent_version_id": str(previous[1]),
            "selection_id": str(selection.id),
            "selection_rank": selection.rank,
        },
    )
    return "reassigned"
