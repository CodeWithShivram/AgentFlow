"""Project progress and time remaining (ADR-0053): honest estimates from stored records only.

Progress is weighted, not a task count:
- each active task weighs its `effort_points` (1 for tasks without an estimate);
- only verified completion counts (COMPLETED, which only platform verification reaches), never
  an agent's own report or work in flight;
- software projects add two milestones: the integration gate (15% of the task weight) and the
  package (5%); neither counts until it actually passed or exists.
It reaches 100% only when the work is really done.

Time remaining is a range with a confidence label, never a countdown:
- samples are the observed durations of successful attempts of completed tasks, per effort point;
- remaining effort x the 25th/75th percentile seconds per point, divided by the parallelism the
  workflow can use, with the upper end widened by 1.5 for retries and review;
- confidence is low under 3 samples, medium under 10, high from 10; with no samples there is
  no estimate, and the response says why.
"""

import math
import uuid
from datetime import datetime
from statistics import quantiles
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentExecution,
    ExecutionStatus,
    HumanIntervention,
    IntegrationStatus,
    InterventionStatus,
    PackageStatus,
    PlanStatus,
    ProjectIntegration,
    ProjectPackage,
    TaskStatus,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
    WorkflowTask,
)
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow

Phase = Literal[
    "PLANNING",
    "AWAITING_APPROVAL",
    "BUILDING",
    "INTEGRATING",
    "PACKAGING",
    "COMPLETED",
    "NEEDS_ATTENTION",
    "FAILED",
    "CANCELLED",
]
Milestone = Literal["not_applicable", "pending", "running", "passed", "failed", "ready"]
Confidence = Literal["none", "low", "medium", "high"]

INTEGRATION_SHARE = 0.15
PACKAGE_SHARE = 0.05
RETRY_FACTOR = 1.5
_IN_FLIGHT = {TaskStatus.QUEUED, TaskStatus.IN_PROGRESS, TaskStatus.SUBMITTED, TaskStatus.VERIFYING}
_ATTENTION = {TaskStatus.ESCALATED, TaskStatus.BLOCKED, TaskStatus.FAILED}


class TaskCounts(BaseModel):
    total: int
    completed: int
    in_progress: int
    waiting: int
    needs_attention: int


class Estimate(BaseModel):
    low_seconds: int | None
    high_seconds: int | None
    confidence: Confidence
    samples: int
    explanation: str


class ProgressResponse(BaseModel):
    phase: Phase
    percent: int
    explanation: str
    tasks: TaskCounts
    completed_weight: int
    total_weight: int
    integration: Milestone
    package: Milestone
    open_requests: int
    remaining: Estimate


def _weight(task: WorkflowTask) -> int:
    return task.effort_points or 1


def _duration_seconds(started: datetime, completed: datetime | None) -> float | None:
    if completed is None:
        return None
    return max((completed - started).total_seconds(), 0.0)


def estimate_remaining(
    remaining_weight: int, per_point_samples: list[float], parallel: int
) -> Estimate:
    samples = len(per_point_samples)
    if remaining_weight <= 0:
        return Estimate(
            low_seconds=0, high_seconds=0, confidence="high" if samples else "none",
            samples=samples, explanation="No task work remains.",
        )  # fmt: skip
    if samples == 0:
        return Estimate(
            low_seconds=None, high_seconds=None, confidence="none", samples=0,
            explanation="No task has finished yet, so there is nothing to estimate from.",
        )  # fmt: skip
    ordered = sorted(per_point_samples)
    if samples >= 4:
        q1, _, q3 = quantiles(ordered, n=4)
    else:
        q1, q3 = ordered[0], ordered[-1]
    lanes = max(parallel, 1)
    low = remaining_weight * q1 / lanes
    high = remaining_weight * q3 * RETRY_FACTOR / lanes
    confidence: Confidence = "low" if samples < 3 else "medium" if samples < 10 else "high"
    return Estimate(
        low_seconds=int(math.floor(low)),
        high_seconds=int(math.ceil(max(high, low))),
        confidence=confidence,
        samples=samples,
        explanation=(
            f"Based on {samples} finished task attempt{'s' if samples != 1 else ''}: "
            f"{remaining_weight} effort point{'s' if remaining_weight != 1 else ''} remain, "
            f"up to {lanes} in parallel; the upper end allows for retries."
        ),
    )


async def project_progress(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> ProgressResponse:
    workflow = await get_workflow(session, access, workflow_id)
    tasks = list(
        (
            await session.execute(
                select(WorkflowTask).where(
                    WorkflowTask.workspace_id == workflow.workspace_id,
                    WorkflowTask.workflow_id == workflow.id,
                    WorkflowTask.removed_at.is_(None),
                )
            )
        ).scalars()
    )
    active = [t for t in tasks if not (t.status == TaskStatus.CANCELLED and not t.required)]
    completed = [t for t in active if t.status == TaskStatus.COMPLETED]
    counts = TaskCounts(
        total=len(active),
        completed=len(completed),
        in_progress=sum(1 for t in active if t.status in _IN_FLIGHT),
        waiting=sum(
            1
            for t in active
            if t.status not in _IN_FLIGHT | _ATTENTION and t.status != TaskStatus.COMPLETED
        ),
        needs_attention=sum(1 for t in active if t.status in _ATTENTION),
    )
    task_weight = sum(_weight(t) for t in active)
    done_weight = sum(_weight(t) for t in completed)
    software = workflow.kind == WorkflowKind.SOFTWARE_PROJECT
    integration, package = (
        await _milestones(session, workflow)
        if software
        else (
            "not_applicable",
            "not_applicable",
        )
    )
    total = task_weight
    done = done_weight
    if software and task_weight:
        integration_weight = max(1, round(task_weight * INTEGRATION_SHARE))
        package_weight = max(1, round(task_weight * PACKAGE_SHARE))
        total += integration_weight + package_weight
        done += integration_weight if integration == "passed" else 0
        done += package_weight if package == "ready" else 0
    finished = workflow.status == WorkflowStatus.COMPLETED and (not software or package == "ready")
    percent = 100 if finished else (min(99, done * 100 // total) if total else 0)
    open_requests = (
        await session.execute(
            select(func.count())
            .select_from(HumanIntervention)
            .where(
                HumanIntervention.workspace_id == workflow.workspace_id,
                HumanIntervention.workflow_id == workflow.id,
                HumanIntervention.status == InterventionStatus.OPEN,
            )
        )
    ).scalar_one()
    phase = _phase(workflow, counts, integration, package, open_requests)
    samples = await _per_point_samples(session, workflow, completed)
    unfinished = [t for t in active if t.status != TaskStatus.COMPLETED]
    remaining = estimate_remaining(
        sum(_weight(t) for t in unfinished),
        samples,
        min(workflow.max_parallel_tasks, max(len(unfinished), 1)),
    )
    return ProgressResponse(
        phase=phase,
        percent=percent,
        explanation=(
            f"{done} of {total} weighted points verified complete"
            + (" (tasks by effort, plus integration and packaging)." if software else ".")
            if total
            else "No tasks have been planned yet."
        ),
        tasks=counts,
        completed_weight=done,
        total_weight=total,
        integration=integration,
        package=package,
        open_requests=open_requests,
        remaining=remaining,
    )


async def _milestones(session: AsyncSession, workflow: Workflow) -> tuple[Milestone, Milestone]:
    latest = (
        await session.execute(
            select(ProjectIntegration.status)
            .where(
                ProjectIntegration.workspace_id == workflow.workspace_id,
                ProjectIntegration.workflow_id == workflow.id,
            )
            .order_by(ProjectIntegration.created_at.desc(), ProjectIntegration.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    states: dict[IntegrationStatus | None, Milestone] = {
        None: "pending",
        IntegrationStatus.RUNNING: "running",
        IntegrationStatus.PASSED: "passed",
        IntegrationStatus.FAILED: "failed",
        IntegrationStatus.NOT_RUN: "failed",
    }
    integration = states[latest]
    ready = (
        await session.execute(
            select(func.count())
            .select_from(ProjectPackage)
            .where(
                ProjectPackage.workspace_id == workflow.workspace_id,
                ProjectPackage.workflow_id == workflow.id,
                ProjectPackage.status == PackageStatus.READY,
            )
        )
    ).scalar_one()
    return integration, "ready" if ready else "pending"


async def _per_point_samples(
    session: AsyncSession, workflow: Workflow, completed: list[WorkflowTask]
) -> list[float]:
    if not completed:
        return []
    weights = {t.id: _weight(t) for t in completed}
    rows = await session.execute(
        select(
            AgentExecution.task_id, AgentExecution.started_at, AgentExecution.completed_at
        ).where(
            AgentExecution.workspace_id == workflow.workspace_id,
            AgentExecution.workflow_id == workflow.id,
            AgentExecution.task_id.in_(list(weights)),
            AgentExecution.status == ExecutionStatus.SUCCEEDED,
        )
    )
    samples = []
    for task_id, started, finished in rows:
        seconds = _duration_seconds(started, finished)
        if seconds is not None:
            samples.append(seconds / weights[task_id])
    return samples


def _phase(
    workflow: Workflow,
    counts: TaskCounts,
    integration: Milestone,
    package: Milestone,
    open_requests: int,
) -> Phase:
    status = workflow.status
    if status == WorkflowStatus.FAILED:
        return "FAILED"
    if status == WorkflowStatus.CANCELLED:
        return "CANCELLED"
    if status == WorkflowStatus.COMPLETED:
        return "PACKAGING" if package == "pending" else "COMPLETED"
    if status in {WorkflowStatus.PAUSED, WorkflowStatus.BLOCKED} or open_requests:
        return "NEEDS_ATTENTION"
    if status in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.READY}:
        return "AWAITING_APPROVAL"
    if status in {WorkflowStatus.PENDING, WorkflowStatus.PLANNING}:
        return "AWAITING_APPROVAL" if workflow.plan_status == PlanStatus.READY else "PLANNING"
    if integration != "not_applicable" and counts.total and counts.completed == counts.total:
        return "INTEGRATING"
    return "BUILDING"
