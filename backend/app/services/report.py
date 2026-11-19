"""The workflow execution report (handoff §1 and §14 screen 9, acceptance 16; ADR-0042).

Composed on read from stored records only: the workflow and its tasks, every attempt with its
verification outcomes, requests for people with their decisions, the event log (for retries,
reassignments, escalations and pauses), failed background jobs, and the usage and cost records
(ADR-0040, ADR-0041). Nothing is estimated or invented. Model and agent text is passed through
as plain text; evaluator findings and people's free text (answers, notes) are left out.
"""

import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    AgentExecution,
    AgentVersion,
    ApprovalDecision,
    ExecutionStatus,
    HumanIntervention,
    InterventionStatus,
    OutboxEvent,
    OutboxStatus,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowTask,
)
from app.repositories import workflows as repository
from app.schemas.report import (
    ReportAttempt,
    ReportDeliverable,
    ReportIntervention,
    ReportMetrics,
    ReportPause,
    ReportRecovery,
    ReportTask,
    ReportVerification,
    ReportWorkflow,
    WorkflowReport,
)
from app.services import analytics, costs
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow
from app.workflows.state_machine import WORKFLOW_TERMINAL

REPORT_VERSION = "report/v1"
# Per deliverable; agents' outputs are bounded (MAX_OUTPUTS_CHARS) but a report stays readable.
MAX_DELIVERABLE_CHARS = 20_000
USAGE_ROWS = 100


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _content(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    if len(text) > MAX_DELIVERABLE_CHARS:
        return text[:MAX_DELIVERABLE_CHARS], True
    return text, False


async def workflow_report(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> WorkflowReport:
    workflow = await get_workflow(session, access, workflow_id)
    now = datetime.now(UTC)
    scope = {"workflow_id": workflow.id, "workspace_id": workflow.workspace_id}

    tasks = await repository.list_tasks(session, workflow)
    depends: dict[uuid.UUID, list[uuid.UUID]] = {}
    for task_id, depends_on in await repository.list_dependencies(session, workflow.id):
        depends.setdefault(task_id, []).append(depends_on)

    executions = list(
        (
            await session.execute(
                select(AgentExecution, AgentVersion.version)
                .join(AgentVersion, AgentVersion.id == AgentExecution.agent_version_id)
                .where(
                    AgentExecution.workflow_id == workflow.id,
                    AgentExecution.workspace_id == workflow.workspace_id,
                )
                .order_by(AgentExecution.task_id, AgentExecution.attempt_number)
            )
        ).all()
    )
    results = list(
        (
            await session.execute(
                select(VerificationResult)
                .where(
                    VerificationResult.workflow_id == workflow.id,
                    VerificationResult.workspace_id == workflow.workspace_id,
                )
                .order_by(VerificationResult.created_at)
            )
        ).scalars()
    )
    agent_ids = {e.agent_id for e, _ in executions} | {
        t.assigned_agent_id for t in tasks if t.assigned_agent_id
    }
    names = dict(
        (await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))).all()
        if agent_ids
        else []
    )
    versions = dict(
        (
            await session.execute(
                select(AgentVersion.id, AgentVersion.version).where(
                    AgentVersion.id.in_(
                        [t.assigned_agent_version_id for t in tasks if t.assigned_agent_version_id]
                    )
                )
            )
        ).all()
    )

    by_execution: dict[uuid.UUID, list[ReportVerification]] = {}
    for result in results:
        by_execution.setdefault(result.execution_id, []).append(
            ReportVerification(
                verifier=result.verifier,
                outcome=result.outcome.value,
                issue_codes=[str(i.get("code")) for i in result.issues if i.get("code")],
            )
        )
    attempts_by_task: dict[uuid.UUID, list[tuple[AgentExecution, int]]] = {}
    for execution, version in executions:
        attempts_by_task.setdefault(execution.task_id, []).append((execution, version))

    report_tasks = [
        _task(task, depends, attempts_by_task.get(task.id, []), by_execution, names, versions)
        for task in tasks
    ]

    interventions = list(
        (
            await session.execute(
                select(HumanIntervention, ApprovalDecision.decided_role)
                .outerjoin(
                    ApprovalDecision, ApprovalDecision.intervention_id == HumanIntervention.id
                )
                .where(
                    HumanIntervention.workflow_id == workflow.id,
                    HumanIntervention.workspace_id == workflow.workspace_id,
                )
                .order_by(HumanIntervention.created_at)
            )
        ).all()
    )
    events = list(
        (
            await session.execute(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.workflow_id == workflow.id,
                    WorkflowEvent.workspace_id == workflow.workspace_id,
                )
                .order_by(WorkflowEvent.sequence)
            )
        ).scalars()
    )
    failed_jobs = (
        await session.execute(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.workflow_id == scope["workflow_id"],
                OutboxEvent.workspace_id == scope["workspace_id"],
                OutboxEvent.status == OutboxStatus.FAILED,
            )
        )
    ).scalar_one()

    attempt_statuses = Counter(e.status.value for e, _ in executions)
    verifications = Counter(r.outcome for r in results)
    return WorkflowReport(
        report_version=REPORT_VERSION,
        generated_at=now,
        final=workflow.status in WORKFLOW_TERMINAL,
        workflow=ReportWorkflow(
            id=workflow.id,
            prompt=workflow.prompt,
            status=workflow.status.value,
            pause_reason=workflow.pause_reason.value if workflow.pause_reason else None,
            autonomy_policy=workflow.autonomy_policy.value,
            budget_limit=workflow.budget_limit,
            currency=workflow.currency,
            max_parallel_tasks=workflow.max_parallel_tasks,
            deadline=workflow.deadline,
            confirmed_plan_version=workflow.confirmed_plan_version,
            created_at=workflow.created_at,
            started_at=workflow.started_at,
            completed_at=workflow.completed_at,
        ),
        tasks=report_tasks,
        interventions=[_intervention(row, role) for row, role in interventions],
        recovery=ReportRecovery(
            retries=sum(max(0, len(a) - 1) for a in attempts_by_task.values()),
            failed_attempts=attempt_statuses[ExecutionStatus.FAILED.value]
            + attempt_statuses[ExecutionStatus.TIMED_OUT.value],
            reassignments=sum(
                1 for e in events if e.event_type == WorkflowEventType.TASK_REASSIGNED
            ),
            escalations=sum(
                1
                for e in events
                if e.task_id is not None and e.payload.get("to") == TaskStatus.ESCALATED.value
            ),
            expired_requests=sum(
                1 for row, _ in interventions if row.status == InterventionStatus.EXPIRED
            ),
            failed_background_jobs=failed_jobs,
            pauses=[
                ReportPause(
                    at=e.created_at,
                    pause_reason=e.payload.get("pause_reason"),
                    actor=e.payload.get("actor"),
                )
                for e in events
                if e.task_id is None and e.payload.get("to") == "PAUSED"
            ],
        ),
        metrics=ReportMetrics(
            tasks=len([t for t in tasks if t.removed_at is None]),
            tasks_by_status=dict(Counter(t.status.value for t in tasks if t.removed_at is None)),
            attempts=len(executions),
            attempts_by_status=dict(attempt_statuses),
            verifications_passed=verifications[VerificationOutcome.PASSED],
            verifications_failed=verifications[VerificationOutcome.FAILED],
            elapsed_seconds=_seconds(workflow.started_at, workflow.completed_at or now),
        ),
        costs=await costs.workflow_costs(session, access, workflow.id),
        usage=await analytics.workflow(session, access, workflow.id, USAGE_ROWS),
        requirements=workflow.normalized_requirements,
    )


def _task(
    task: WorkflowTask,
    depends: dict[uuid.UUID, list[uuid.UUID]],
    attempts: list[tuple[AgentExecution, int]],
    verification: dict[uuid.UUID, list[ReportVerification]],
    names: dict[uuid.UUID, str],
    versions: dict[uuid.UUID, int],
) -> ReportTask:
    deliverables: list[ReportDeliverable] = []
    if task.status == TaskStatus.COMPLETED:
        accepted = next(
            (e for e, _ in reversed(attempts) if e.status == ExecutionStatus.SUCCEEDED), None
        )
        outputs: dict[str, Any] = ((accepted.submission or {}) if accepted else {}).get(
            "outputs", {}
        )
        for name in task.deliverables:
            content, truncated = _content(outputs.get(name))
            deliverables.append(ReportDeliverable(name=name, content=content, truncated=truncated))
    return ReportTask(
        id=task.id,
        title=task.title,
        status=task.status.value,
        required=task.required,
        removed=task.removed_at is not None,
        priority=task.priority,
        required_skills=list(task.required_skills),
        depends_on=sorted(depends.get(task.id, [])),
        assigned_agent_id=task.assigned_agent_id,
        assigned_agent_name=names.get(task.assigned_agent_id) if task.assigned_agent_id else None,
        assigned_agent_version=(
            versions.get(task.assigned_agent_version_id) if task.assigned_agent_version_id else None
        ),
        started_at=task.started_at,
        completed_at=task.completed_at,
        attempts=[
            ReportAttempt(
                attempt_number=execution.attempt_number,
                agent_id=execution.agent_id,
                agent_name=names.get(execution.agent_id, "Removed agent"),
                agent_version=version,
                status=execution.status.value,
                error_code=execution.error_code,
                started_at=execution.started_at,
                completed_at=execution.completed_at,
                duration_seconds=_seconds(execution.started_at, execution.completed_at),
                verification=verification.get(execution.id, []),
            )
            for execution, version in attempts
        ],
        deliverables=deliverables,
    )


def _intervention(row: HumanIntervention, decided_role: str | None) -> ReportIntervention:
    resolution = row.resolution or {}
    return ReportIntervention(
        id=row.id,
        task_id=row.task_id,
        type=row.type.value,
        subject=row.subject,
        status=row.status.value,
        requested_action=row.requested_action,
        requested_by=row.requested_by,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        decision=resolution.get("decision"),
        then=resolution.get("then"),
        outcome=resolution.get("outcome"),
        decided_role=decided_role,
    )
