"""Analytics API models (ADR-0041). Amounts are decimal strings; actual, estimated and mock costs
are reported apart and never added together (ADR-0040)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

GroupBy = Literal["workflow", "task", "agent", "model", "purpose", "day"]


class CostByCurrency(BaseModel):
    currency: str
    actual: Decimal
    estimated: Decimal
    # The mock provider's price: never a real charge.
    mock: Decimal


class UsageTotals(BaseModel):
    calls: int
    # Calls without a price or without reported counts.
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    costs: list[CostByCurrency]


class BreakdownRow(UsageTotals):
    # The group's identifier: an id, "provider/model", a purpose, an ISO date, or
    # "platform:<purpose>" for calls no agent made (analysis, planning, evaluation).
    key: str
    # A readable name: a workflow's prompt start, a task title, an agent name...
    label: str


class TokenRow(BaseModel):
    key: str
    label: str
    calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int


class Range(BaseModel):
    start: datetime
    end: datetime


class OverviewResponse(BaseModel):
    range: Range
    totals: UsageTotals
    # Distinct workflows and agents with model calls in the range.
    workflows: int
    agents: int


class CostBreakdownResponse(BaseModel):
    group_by: GroupBy
    range: Range
    # The zone that cut days for group_by=day (ADR-0045).
    time_zone: str
    rows: list[BreakdownRow]
    # Over every call in the range, including rows cut by `limit`; the rows add up to it.
    totals: UsageTotals
    truncated: bool


class TokenBreakdownResponse(BaseModel):
    group_by: GroupBy
    range: Range
    time_zone: str
    rows: list[TokenRow]
    totals: UsageTotals
    truncated: bool


class WorkflowAnalyticsResponse(BaseModel):
    workflow_id: uuid.UUID
    totals: UsageTotals
    by_task: list[BreakdownRow]
    by_agent: list[BreakdownRow]
    by_model: list[BreakdownRow]
