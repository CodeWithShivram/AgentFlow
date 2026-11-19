"""Usage and cost breakdowns (ADR-0041): by workflow, task, agent, model, purpose or day.

Every figure is aggregated from the stored `llm_usage` rows of one workspace (the reporting
rule: compose persisted facts, never invent metrics). A call is attributed to an agent through
its attempt (`agent_executions`); calls no agent made (analysis, planning, evaluation) are
grouped as "platform" by purpose, so every breakdown adds up to the same totals. Costs stay per
currency and per basis (actual, estimated, mock) and are never added together (ADR-0040).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Agent, AgentExecution, CostBasis, LlmUsage, Workflow, WorkflowTask
from app.schemas.analytics import (
    BreakdownRow,
    CostBreakdownResponse,
    CostByCurrency,
    GroupBy,
    OverviewResponse,
    Range,
    TokenBreakdownResponse,
    TokenRow,
    UsageTotals,
    WorkflowAnalyticsResponse,
)
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow

DEFAULT_RANGE = timedelta(days=30)
MAX_RANGE = timedelta(days=366)
LABEL_LENGTH = 120
PLATFORM = "platform:"
# The platform's own model calls (analysis, planning, semantic evaluation). An evaluation records
# the attempt it judges, but its cost is the platform's, not the evaluated agent's.
PLATFORM_PURPOSES = frozenset({"prompt_analysis", "plan_generation", "semantic_evaluation"})


def _is_platform() -> ColumnElement[bool]:
    return AgentExecution.agent_id.is_(None) | LlmUsage.purpose.in_(PLATFORM_PURPOSES)


class AnalyticsRangeInvalidError(AppError):
    status_code = 422
    code = "ANALYTICS_RANGE_INVALID"
    message = "The range must start before it ends and span at most 366 days."


def resolve_range(start: datetime | None, end: datetime | None) -> Range:
    """Defaults to the last 30 days; at most 366 days."""
    end = end or datetime.now(UTC)
    start = start or end - DEFAULT_RANGE
    if start.tzinfo is None or end.tzinfo is None or start >= end or end - start > MAX_RANGE:
        raise AnalyticsRangeInvalidError()
    return Range(start=start, end=end)


# --- aggregation ------------------------------------------------------------------------------


@dataclass
class _Totals:
    calls: int = 0
    unpriced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    costs: dict[str, dict[CostBasis, Decimal]] = field(default_factory=dict)

    def add(
        self,
        *,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        currency: str | None,
        basis: CostBasis | None,
        cost: Decimal | None,
    ) -> None:
        self.calls += calls
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached_input_tokens
        if basis is None or currency is None or cost is None:
            self.unpriced_calls += calls
            return
        by_basis = self.costs.setdefault(currency, {})
        by_basis[basis] = by_basis.get(basis, Decimal(0)) + cost

    def merge(self, other: "_Totals") -> None:
        self.calls += other.calls
        self.unpriced_calls += other.unpriced_calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens += other.cached_input_tokens
        for currency, by_basis in other.costs.items():
            mine = self.costs.setdefault(currency, {})
            for basis, amount in by_basis.items():
                mine[basis] = mine.get(basis, Decimal(0)) + amount

    def model(self) -> UsageTotals:
        return UsageTotals(**self._fields())

    def _fields(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "unpriced_calls": self.unpriced_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "costs": [
                CostByCurrency(
                    currency=currency,
                    actual=by_basis.get(CostBasis.ACTUAL, Decimal(0)),
                    estimated=by_basis.get(CostBasis.ESTIMATED, Decimal(0)),
                    mock=by_basis.get(CostBasis.MOCK, Decimal(0)),
                )
                for currency, by_basis in sorted(self.costs.items())
            ],
        }

    def row(self, key: str, label: str) -> BreakdownRow:
        return BreakdownRow(key=key, label=label, **self._fields())


def _keys(group_by: GroupBy | None) -> list[Any]:
    if group_by is None:
        return []
    if group_by == "workflow":
        return [LlmUsage.workflow_id]
    if group_by == "task":
        return [LlmUsage.task_id]
    if group_by == "model":
        return [LlmUsage.provider, LlmUsage.model]
    if group_by == "purpose":
        return [LlmUsage.purpose]
    if group_by == "day":
        return [func.date(func.timezone("UTC", LlmUsage.created_at))]
    # agent: the attempt's agent; the platform's calls are grouped by purpose instead.
    return [
        case((_is_platform(), None), else_=AgentExecution.agent_id),
        case((_is_platform(), LlmUsage.purpose), else_=None),
    ]


async def _aggregate(
    session: AsyncSession,
    access: WorkspaceAccess,
    group_by: GroupBy | None,
    conditions: Sequence[ColumnElement[bool]],
) -> dict[tuple[Any, ...], _Totals]:
    keys = _keys(group_by)
    statement = (
        select(
            *keys,
            LlmUsage.currency,
            LlmUsage.cost_basis,
            func.count(),
            func.coalesce(func.sum(LlmUsage.input_tokens), 0),
            func.coalesce(func.sum(LlmUsage.output_tokens), 0),
            func.coalesce(func.sum(LlmUsage.cached_input_tokens), 0),
            func.sum(LlmUsage.total_cost),
        )
        .select_from(LlmUsage)
        .outerjoin(
            AgentExecution,
            (AgentExecution.id == LlmUsage.execution_id)
            & (AgentExecution.workspace_id == LlmUsage.workspace_id),
        )
        .where(LlmUsage.workspace_id == access.workspace_id, *conditions)
        .group_by(*keys, LlmUsage.currency, LlmUsage.cost_basis)
    )
    groups: dict[tuple[Any, ...], _Totals] = {}
    width = len(keys)
    for row in (await session.execute(statement)).all():
        key = tuple(row[:width])
        currency, basis, calls, input_tokens, output_tokens, cached, cost = row[width:]
        groups.setdefault(key, _Totals()).add(
            calls=calls,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cached_input_tokens=int(cached),
            currency=currency,
            basis=CostBasis(basis) if basis is not None else None,
            cost=cost,
        )
    return groups


def _in_range(window: Range) -> list[ColumnElement[bool]]:
    return [LlmUsage.created_at >= window.start, LlmUsage.created_at < window.end]


# --- labels -------------------------------------------------------------------------------------


async def _names(
    session: AsyncSession, access: WorkspaceAccess, group_by: GroupBy, ids: Iterable[Any]
) -> dict[Any, str]:
    wanted = [i for i in ids if isinstance(i, uuid.UUID)]
    if not wanted:
        return {}
    if group_by == "workflow":
        rows = await session.execute(
            select(Workflow.id, Workflow.prompt).where(
                Workflow.id.in_(wanted), Workflow.workspace_id == access.workspace_id
            )
        )
    elif group_by == "task":
        rows = await session.execute(
            select(WorkflowTask.id, WorkflowTask.title).where(
                WorkflowTask.id.in_(wanted), WorkflowTask.workspace_id == access.workspace_id
            )
        )
    else:  # agent: workspace agents and built-ins (workspace_id NULL)
        rows = await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(wanted)))
    return {row[0]: " ".join(str(row[1]).split())[:LABEL_LENGTH] for row in rows.all()}


def _key_and_label(
    group_by: GroupBy, key: tuple[Any, ...], names: dict[Any, str]
) -> tuple[str, str]:
    if group_by == "model":
        provider, model = key
        return f"{provider}/{model}", f"{provider} · {model}"
    if group_by == "purpose":
        return str(key[0]), str(key[0])
    if group_by == "day":
        day: date = key[0]
        return day.isoformat(), day.isoformat()
    if group_by == "agent":
        agent_id, purpose = key
        if agent_id is None:
            return f"{PLATFORM}{purpose}", f"Platform: {purpose}"
        return str(agent_id), names.get(agent_id, "Removed agent")
    value = key[0]
    if value is None:
        return "none", "No workflow" if group_by == "workflow" else "No task"
    fallback = "Removed workflow" if group_by == "workflow" else "Removed task"
    return str(value), names.get(value, fallback)


async def _breakdown(
    session: AsyncSession,
    access: WorkspaceAccess,
    group_by: GroupBy,
    conditions: Sequence[ColumnElement[bool]],
    limit: int,
) -> tuple[list[BreakdownRow], _Totals, bool]:
    groups = await _aggregate(session, access, group_by, conditions)
    names: dict[Any, str] = {}
    if group_by in ("workflow", "task", "agent"):
        names = await _names(session, access, group_by, (key[0] for key in groups))
    totals = _Totals()
    rows = []
    for key, group in groups.items():
        totals.merge(group)
        rows.append(group.row(*_key_and_label(group_by, key, names)))
    if group_by == "day":
        rows.sort(key=lambda r: r.key)
    else:
        rows.sort(key=lambda r: (-r.calls, r.label, r.key))
    return rows[:limit], totals, len(rows) > limit


# --- endpoints --------------------------------------------------------------------------------


async def overview(
    session: AsyncSession, access: WorkspaceAccess, window: Range
) -> OverviewResponse:
    conditions = _in_range(window)
    groups = await _aggregate(session, access, None, conditions)
    totals = groups.get((), _Totals())
    counts = (
        await session.execute(
            select(
                func.count(func.distinct(LlmUsage.workflow_id)),
                func.count(
                    func.distinct(case((_is_platform(), None), else_=AgentExecution.agent_id))
                ),
            )
            .select_from(LlmUsage)
            .outerjoin(
                AgentExecution,
                (AgentExecution.id == LlmUsage.execution_id)
                & (AgentExecution.workspace_id == LlmUsage.workspace_id),
            )
            .where(LlmUsage.workspace_id == access.workspace_id, *conditions)
        )
    ).one()
    return OverviewResponse(
        range=window, totals=totals.model(), workflows=counts[0], agents=counts[1]
    )


async def costs(
    session: AsyncSession,
    access: WorkspaceAccess,
    window: Range,
    group_by: GroupBy,
    *,
    workflow_id: uuid.UUID | None,
    limit: int,
) -> CostBreakdownResponse:
    conditions = _in_range(window)
    if workflow_id is not None:
        conditions.append(LlmUsage.workflow_id == workflow_id)
    rows, totals, truncated = await _breakdown(session, access, group_by, conditions, limit)
    return CostBreakdownResponse(
        group_by=group_by, range=window, rows=rows, totals=totals.model(), truncated=truncated
    )


async def tokens(
    session: AsyncSession,
    access: WorkspaceAccess,
    window: Range,
    group_by: GroupBy,
    *,
    workflow_id: uuid.UUID | None,
    limit: int,
) -> TokenBreakdownResponse:
    result = await costs(session, access, window, group_by, workflow_id=workflow_id, limit=limit)
    return TokenBreakdownResponse(
        group_by=group_by,
        range=window,
        rows=[
            TokenRow(
                key=r.key,
                label=r.label,
                calls=r.calls,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cached_input_tokens=r.cached_input_tokens,
            )
            for r in result.rows
        ],
        totals=result.totals,
        truncated=result.truncated,
    )


async def workflow(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID, limit: int
) -> WorkflowAnalyticsResponse:
    """One workflow's whole history, by task, agent and model (404 outside the workspace)."""
    found = await get_workflow(session, access, workflow_id)
    conditions = [LlmUsage.workflow_id == found.id]
    by_task, totals, _ = await _breakdown(session, access, "task", conditions, limit)
    by_agent, _, _ = await _breakdown(session, access, "agent", conditions, limit)
    by_model, _, _ = await _breakdown(session, access, "model", conditions, limit)
    return WorkflowAnalyticsResponse(
        workflow_id=found.id,
        totals=totals.model(),
        by_task=by_task,
        by_agent=by_agent,
        by_model=by_model,
    )
