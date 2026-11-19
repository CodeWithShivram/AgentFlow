"""Latency, retry and failure metrics (ADR-0043), from stored records only.

Each measurement counts in the period it finished: model and tool calls when recorded,
attempts and workflows when completed, verification results when stored. Unfinished work is
"in flight" and kept out of durations and rates. Cancelled attempts are counted but left out of
failure rates: a cancellation is not the agent failing. Every rate carries its numerator and
denominator, and a rate or distribution with nothing behind it is null, never zero.
"""

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    AgentExecution,
    ExecutionStatus,
    LlmCallStatus,
    LlmUsage,
    TaskStatus,
    ToolInvocation,
    ToolInvocationStatus,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.schemas.analytics import Range
from app.schemas.metrics import (
    AttemptGroup,
    AttemptMetrics,
    Distribution,
    MetricsResponse,
    ModelCallGroup,
    ModelCallMetrics,
    Rate,
    TaskMetrics,
    ToolGroup,
    ToolMetrics,
    VerificationMetrics,
    WorkflowMetrics,
)
from app.services.authorization import WorkspaceAccess

GROUP_LIMIT = 50
_FINISHED_WORKFLOW = (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED)
_IN_FLIGHT_WORKFLOW = (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED, WorkflowStatus.BLOCKED)


def rate(numerator: int, denominator: int) -> Rate:
    return Rate(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6) if denominator else None,
    )


def _distribution_columns(expr: Any) -> list[Any]:
    return [
        func.count(expr),
        func.percentile_cont(0.5).within_group(expr),
        func.percentile_cont(0.95).within_group(expr),
        func.max(expr),
        func.avg(expr),
    ]


def _distribution(unit: Literal["ms", "s"], values: Sequence[Any]) -> Distribution:
    samples, p50, p95, high, mean = values

    def number(value: Any) -> float | None:
        return round(float(value), 3) if samples and value is not None else None

    return Distribution(
        unit=unit,
        samples=int(samples or 0),
        p50=number(p50),
        p95=number(p95),
        max=number(high),
        mean=number(mean),
    )


def _seconds(start: Any, end: Any) -> Any:
    return func.extract("epoch", end - start)


async def _counts(
    session: AsyncSession, column: Any, conditions: Sequence[ColumnElement[bool]]
) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).where(*conditions).group_by(column))
    return {str(getattr(key, "value", key)): int(count) for key, count in rows.all()}


# --- model calls --------------------------------------------------------------------------------


async def _model_calls(
    session: AsyncSession, access: WorkspaceAccess, window: Range
) -> ModelCallMetrics:
    where = [
        LlmUsage.workspace_id == access.workspace_id,
        LlmUsage.created_at >= window.start,
        LlmUsage.created_at < window.end,
    ]
    by_status = await _counts(session, LlmUsage.status, where)
    calls = sum(by_status.values())
    failed = by_status.get(LlmCallStatus.FAILED.value, 0) + by_status.get(
        LlmCallStatus.INVALID_OUTPUT.value, 0
    )
    overall = (
        await session.execute(select(*_distribution_columns(LlmUsage.latency_ms)).where(*where))
    ).one()
    failing = func.count().filter(LlmUsage.status != LlmCallStatus.SUCCEEDED)
    rows = await session.execute(
        select(
            LlmUsage.provider,
            LlmUsage.model,
            func.count(),
            failing,
            *_distribution_columns(LlmUsage.latency_ms),
        )
        .where(*where)
        .group_by(LlmUsage.provider, LlmUsage.model)
        .order_by(func.count().desc(), LlmUsage.provider, LlmUsage.model)
        .limit(GROUP_LIMIT)
    )
    return ModelCallMetrics(
        calls=calls,
        by_status=by_status,
        failure_rate=rate(failed, calls),
        latency=_distribution("ms", overall),
        by_model=[
            ModelCallGroup(
                key=f"{provider}/{model}",
                label=f"{provider} · {model}",
                calls=count,
                failure_rate=rate(fails, count),
                latency=_distribution("ms", rest),
            )
            for provider, model, count, fails, *rest in rows.all()
        ],
    )


# --- attempts and tasks -------------------------------------------------------------------------


async def _attempts(
    session: AsyncSession, access: WorkspaceAccess, window: Range
) -> AttemptMetrics:
    finished = [
        AgentExecution.workspace_id == access.workspace_id,
        AgentExecution.status != ExecutionStatus.RUNNING,
        AgentExecution.completed_at >= window.start,
        AgentExecution.completed_at < window.end,
    ]
    by_status = await _counts(session, AgentExecution.status, finished)
    total = sum(by_status.values())
    failures = by_status.get(ExecutionStatus.FAILED.value, 0) + by_status.get(
        ExecutionStatus.TIMED_OUT.value, 0
    )
    not_cancelled = total - by_status.get(ExecutionStatus.CANCELLED.value, 0)
    retries = (
        await session.execute(
            select(func.count()).where(*finished, AgentExecution.attempt_number > 1)
        )
    ).scalar_one()
    in_flight = (
        await session.execute(
            select(func.count()).where(
                AgentExecution.workspace_id == access.workspace_id,
                AgentExecution.status == ExecutionStatus.RUNNING,
                AgentExecution.started_at < window.end,
            )
        )
    ).scalar_one()
    duration = _seconds(AgentExecution.started_at, AgentExecution.completed_at)
    overall = (
        await session.execute(select(*_distribution_columns(duration)).where(*finished))
    ).one()

    failing = func.count().filter(
        AgentExecution.status.in_([ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT])
    )
    cancelled = func.count().filter(AgentExecution.status == ExecutionStatus.CANCELLED)
    retried = func.count().filter(AgentExecution.attempt_number > 1)
    rows = await session.execute(
        select(
            AgentExecution.agent_id,
            Agent.name,
            func.count(),
            failing,
            cancelled,
            retried,
            *_distribution_columns(duration),
        )
        .join(Agent, Agent.id == AgentExecution.agent_id)
        .where(*finished)
        .group_by(AgentExecution.agent_id, Agent.name)
        .order_by(func.count().desc(), Agent.name)
        .limit(GROUP_LIMIT)
    )
    return AttemptMetrics(
        finished=total,
        by_status=by_status,
        in_flight=in_flight,
        failure_rate=rate(failures, not_cancelled),
        retry_rate=rate(retries, total),
        duration=_distribution("s", overall),
        by_agent=[
            AttemptGroup(
                key=str(agent_id),
                label=name,
                attempts=count,
                failure_rate=rate(fails, count - cancels),
                retry_rate=rate(retry_count, count),
                duration=_distribution("s", rest),
            )
            for agent_id, name, count, fails, cancels, retry_count, *rest in rows.all()
        ],
    )


async def _tasks(session: AsyncSession, access: WorkspaceAccess, window: Range) -> TaskMetrics:
    completed = [
        WorkflowTask.workspace_id == access.workspace_id,
        WorkflowTask.status == TaskStatus.COMPLETED,
        WorkflowTask.completed_at >= window.start,
        WorkflowTask.completed_at < window.end,
    ]
    # The accepted attempt is the task's latest successful one (ADR-0034).
    accepted = (
        select(func.max(AgentExecution.attempt_number))
        .where(
            AgentExecution.task_id == WorkflowTask.id,
            AgentExecution.workspace_id == WorkflowTask.workspace_id,
            AgentExecution.status == ExecutionStatus.SUCCEEDED,
        )
        .correlate(WorkflowTask)
        .scalar_subquery()
    )
    total, first = (
        await session.execute(
            select(func.count(), func.count().filter(accepted == 1)).where(*completed)
        )
    ).one()
    return TaskMetrics(completed=total, first_attempt=rate(first, total))


# --- verification, tools, workflows -------------------------------------------------------------


async def _verification(
    session: AsyncSession, access: WorkspaceAccess, window: Range
) -> VerificationMetrics:
    where = [
        VerificationResult.workspace_id == access.workspace_id,
        VerificationResult.created_at >= window.start,
        VerificationResult.created_at < window.end,
    ]
    rates = {}
    for kind in ("deterministic", "semantic"):
        total, passed = (
            await session.execute(
                select(
                    func.count(),
                    func.count().filter(VerificationResult.outcome == VerificationOutcome.PASSED),
                ).where(*where, VerificationResult.verifier.like(f"{kind}/%"))
            )
        ).one()
        rates[kind] = rate(passed, total)
    return VerificationMetrics(
        deterministic_pass_rate=rates["deterministic"], semantic_pass_rate=rates["semantic"]
    )


async def _tools(session: AsyncSession, access: WorkspaceAccess, window: Range) -> ToolMetrics:
    where = [
        ToolInvocation.workspace_id == access.workspace_id,
        ToolInvocation.created_at >= window.start,
        ToolInvocation.created_at < window.end,
    ]
    by_status = await _counts(session, ToolInvocation.status, where)
    calls = sum(by_status.values())
    overall = (
        await session.execute(
            select(*_distribution_columns(ToolInvocation.duration_ms)).where(*where)
        )
    ).one()
    failing = func.count().filter(ToolInvocation.status == ToolInvocationStatus.FAILED)
    denied = func.count().filter(ToolInvocation.status == ToolInvocationStatus.DENIED)
    rows = await session.execute(
        select(
            ToolInvocation.tool_name,
            func.count(),
            failing,
            denied,
            *_distribution_columns(ToolInvocation.duration_ms),
        )
        .where(*where)
        .group_by(ToolInvocation.tool_name)
        .order_by(func.count().desc(), ToolInvocation.tool_name)
        .limit(GROUP_LIMIT)
    )
    return ToolMetrics(
        calls=calls,
        by_status=by_status,
        failure_rate=rate(by_status.get(ToolInvocationStatus.FAILED.value, 0), calls),
        denial_rate=rate(by_status.get(ToolInvocationStatus.DENIED.value, 0), calls),
        duration=_distribution("ms", overall),
        by_tool=[
            ToolGroup(
                key=name,
                label=name,
                calls=count,
                failure_rate=rate(fails, count),
                denial_rate=rate(denials, count),
                duration=_distribution("ms", rest),
            )
            for name, count, fails, denials, *rest in rows.all()
        ],
    )


async def _workflows(
    session: AsyncSession, access: WorkspaceAccess, window: Range
) -> WorkflowMetrics:
    finished = [
        Workflow.workspace_id == access.workspace_id,
        Workflow.status.in_(_FINISHED_WORKFLOW),
        Workflow.completed_at >= window.start,
        Workflow.completed_at < window.end,
    ]
    by_status = await _counts(session, Workflow.status, finished)
    duration = _seconds(Workflow.started_at, Workflow.completed_at)
    overall = (
        await session.execute(
            select(*_distribution_columns(duration)).where(
                and_(*finished, Workflow.started_at.is_not(None))
            )
        )
    ).one()
    in_flight = (
        await session.execute(
            select(func.count()).where(
                Workflow.workspace_id == access.workspace_id,
                Workflow.status.in_(_IN_FLIGHT_WORKFLOW),
            )
        )
    ).scalar_one()
    return WorkflowMetrics(
        finished=sum(by_status.values()),
        by_status=by_status,
        in_flight=in_flight,
        duration=_distribution("s", overall),
    )


async def metrics(session: AsyncSession, access: WorkspaceAccess, window: Range) -> MetricsResponse:
    return MetricsResponse(
        range=window,
        model_calls=await _model_calls(session, access, window),
        attempts=await _attempts(session, access, window),
        tasks=await _tasks(session, access, window),
        verification=await _verification(session, access, window),
        tools=await _tools(session, access, window),
        workflows=await _workflows(session, access, window),
    )
