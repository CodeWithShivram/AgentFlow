"""Cost of model calls from their pricing snapshot (handoff §12, ADR-0040).

Pure arithmetic plus read-only queries; no gateway or provider code, so the API may use it.

    cost = (billable_input * input_price + cached * cached_price + output * output_price) / 1e6

Cached input tokens are billed at the model's cached-input price, or at the input price when
the list has none (no discount is invented). A cost is computed only when the provider reported
both input and output counts; the basis says what it is: `actual` (reported counts, real price),
`estimated` (estimated counts, OD-21) or `mock` (the mock provider: never a real charge).
"""

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CostBasis, LlmUsage, ModelPrice, PricingVersion, UsageSource, Workflow

MILLION = Decimal(1_000_000)
# llm_usage cost columns are Numeric(18, 8).
QUANTUM = Decimal("0.00000001")
# Spend that counts toward a workflow's budget (owner decision 2026-11-18): never mock.
COUNTED_BASES = (CostBasis.ACTUAL, CostBasis.ESTIMATED)


@dataclass(frozen=True)
class CallCost:
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal


def _round(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def compute_cost(
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    price: ModelPrice,
) -> CallCost | None:
    """The cost of one call at `price`; None when the provider did not report both counts."""
    if input_tokens is None or output_tokens is None:
        return None
    cached = min(cached_input_tokens or 0, input_tokens)
    cached_rate = (
        price.cached_input_price_per_million
        if price.cached_input_price_per_million is not None
        else price.input_price_per_million
    )
    input_cost = _round(
        (Decimal(input_tokens - cached) * price.input_price_per_million + cached * cached_rate)
        / MILLION
    )
    output_cost = _round(Decimal(output_tokens) * price.output_price_per_million / MILLION)
    return CallCost(input_cost, output_cost, input_cost + output_cost)


def cost_basis(price: ModelPrice, usage_source: UsageSource) -> CostBasis | None:
    if price.is_mock:
        return CostBasis.MOCK
    if usage_source == UsageSource.REPORTED:
        return CostBasis.ACTUAL
    if usage_source == UsageSource.ESTIMATED:
        return CostBasis.ESTIMATED
    return None


def apply_cost(row: LlmUsage, price: ModelPrice, version: PricingVersion) -> None:
    """Fills a usage row's cost columns from its price, all together, or leaves them empty."""
    cost = compute_cost(row.input_tokens, row.output_tokens, row.cached_input_tokens, price)
    basis = cost_basis(price, row.usage_source)
    if cost is None or basis is None:
        return
    row.input_cost = cost.input_cost
    row.output_cost = cost.output_cost
    row.total_cost = cost.total_cost
    row.currency = version.currency
    row.pricing_version = version.version
    row.cost_basis = basis


# --- per-workflow totals and reconciliation -------------------------------------------------


@dataclass
class Totals:
    currency: str
    actual: Decimal = Decimal(0)
    estimated: Decimal = Decimal(0)
    mock: Decimal = Decimal(0)


@dataclass
class WorkflowCosts:
    calls: int = 0
    calls_by_status: dict[str, int] = field(default_factory=dict)
    unpriced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    totals: list[Totals] = field(default_factory=list)
    # Reconciliation (handoff scenario 17): each priced call recomputed from its own snapshot.
    checked_calls: int = 0
    mismatched_calls: int = 0


def workflow_spend(session: Session, workflow: Workflow) -> Decimal:
    """Actual plus estimated spend in the workflow's currency (what counts toward its budget)."""
    total = session.execute(
        select(func.coalesce(func.sum(LlmUsage.total_cost), 0)).where(
            LlmUsage.workflow_id == workflow.id,
            LlmUsage.workspace_id == workflow.workspace_id,
            LlmUsage.cost_basis.in_(COUNTED_BASES),
            LlmUsage.currency == workflow.currency,
        )
    ).scalar_one()
    return Decimal(total or 0)


def workflow_costs(
    session: Session, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowCosts:
    """Every call of the workflow: counts, tokens, totals by currency and basis, and whether
    each stored cost still equals its recomputation from the linked price snapshot."""
    rows = session.execute(
        select(LlmUsage, ModelPrice, PricingVersion)
        .outerjoin(ModelPrice, ModelPrice.id == LlmUsage.model_price_id)
        .outerjoin(PricingVersion, PricingVersion.id == ModelPrice.pricing_version_id)
        .where(LlmUsage.workflow_id == workflow_id, LlmUsage.workspace_id == workspace_id)
    ).all()
    result = WorkflowCosts()
    totals: dict[str, Totals] = {}
    for usage, price, version in rows:
        result.calls += 1
        status = usage.status.value
        result.calls_by_status[status] = result.calls_by_status.get(status, 0) + 1
        result.input_tokens += usage.input_tokens or 0
        result.output_tokens += usage.output_tokens or 0
        result.cached_input_tokens += usage.cached_input_tokens or 0
        if usage.total_cost is None or usage.cost_basis is None or usage.currency is None:
            result.unpriced_calls += 1
            continue
        bucket = totals.setdefault(usage.currency, Totals(currency=usage.currency))
        if usage.cost_basis == CostBasis.ACTUAL:
            bucket.actual += usage.total_cost
        elif usage.cost_basis == CostBasis.ESTIMATED:
            bucket.estimated += usage.total_cost
        else:
            bucket.mock += usage.total_cost
        result.checked_calls += 1
        expected = (
            compute_cost(usage.input_tokens, usage.output_tokens, usage.cached_input_tokens, price)
            if price is not None
            else None
        )
        if (
            expected is None
            or version is None
            or (usage.input_cost, usage.output_cost, usage.total_cost)
            != (expected.input_cost, expected.output_cost, expected.total_cost)
            or (usage.currency, usage.pricing_version) != (version.currency, version.version)
            or usage.cost_basis != cost_basis(price, usage.usage_source)
        ):
            result.mismatched_calls += 1
    result.totals = sorted(totals.values(), key=lambda t: t.currency)
    return result
