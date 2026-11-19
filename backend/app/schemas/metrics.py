"""Latency, retry and failure metrics (ADR-0043). Every rate carries its numerator and
denominator; a rate or distribution with nothing behind it is null ("not measured"), never 0."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.analytics import Range


class Rate(BaseModel):
    numerator: int
    denominator: int
    # numerator / denominator, or null when the denominator is 0.
    value: float | None


class Distribution(BaseModel):
    unit: Literal["ms", "s"]
    samples: int
    # Exact percentiles over the samples (PostgreSQL percentile_cont); null without samples.
    p50: float | None
    p95: float | None
    max: float | None
    mean: float | None


class ModelCallGroup(BaseModel):
    key: str
    label: str
    calls: int
    failure_rate: Rate
    latency: Distribution


class ModelCallMetrics(BaseModel):
    calls: int
    by_status: dict[str, int]
    # FAILED and INVALID_OUTPUT calls over all calls.
    failure_rate: Rate
    latency: Distribution
    by_model: list[ModelCallGroup]


class AttemptGroup(BaseModel):
    key: str
    label: str
    attempts: int
    failure_rate: Rate
    retry_rate: Rate
    duration: Distribution


class AttemptMetrics(BaseModel):
    # Attempts that finished in the range, by status.
    finished: int
    by_status: dict[str, int]
    # Attempts running now (not in durations or rates).
    in_flight: int
    # FAILED and TIMED_OUT over finished attempts that were not cancelled.
    failure_rate: Rate
    # Attempts after a task's first one, over finished attempts.
    retry_rate: Rate
    duration: Distribution
    by_agent: list[AttemptGroup]


class TaskMetrics(BaseModel):
    # Tasks completed in the range.
    completed: int
    # Completed tasks whose accepted attempt was their first.
    first_attempt: Rate


class VerificationMetrics(BaseModel):
    deterministic_pass_rate: Rate
    semantic_pass_rate: Rate


class ToolGroup(BaseModel):
    key: str
    label: str
    calls: int
    failure_rate: Rate
    denial_rate: Rate
    duration: Distribution


class ToolMetrics(BaseModel):
    calls: int
    by_status: dict[str, int]
    failure_rate: Rate
    denial_rate: Rate
    duration: Distribution
    by_tool: list[ToolGroup]


class WorkflowMetrics(BaseModel):
    # Workflows that finished in the range: completed, failed or cancelled.
    finished: int
    by_status: dict[str, int]
    # Started workflows that have not finished (running, paused or blocked).
    in_flight: int
    # From start to finish, for finished workflows that started.
    duration: Distribution


class MetricsResponse(BaseModel):
    range: Range
    model_calls: ModelCallMetrics
    attempts: AttemptMetrics
    tasks: TaskMetrics
    verification: VerificationMetrics
    tools: ToolMetrics
    workflows: WorkflowMetrics
