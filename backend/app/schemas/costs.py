"""Workflow cost API models (ADR-0040). Amounts are decimal strings; actual, estimated and
mock costs are never added together as one charge."""

from decimal import Decimal

from pydantic import BaseModel


class CostTotals(BaseModel):
    currency: str
    actual: Decimal
    estimated: Decimal
    # The mock provider's price: never a real charge.
    mock: Decimal


class BudgetStatus(BaseModel):
    limit: Decimal
    currency: str
    # False when no call was priced in the budget's currency but others were: no exchange rates
    # are used, so the budget is not enforced (ADR-0040).
    comparable: bool
    # Actual plus estimated spend in the budget's currency (mock never counts).
    counted_spend: Decimal
    reached: bool


class WorkflowCostsResponse(BaseModel):
    calls: int
    calls_by_status: dict[str, int]
    # Calls without a price or without reported counts.
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    totals: list[CostTotals]
    budget: BudgetStatus | None
    # Handoff scenario 17: every priced call recomputed from its own price snapshot.
    checked_calls: int
    mismatched_calls: int
    reconciled: bool
