"""Per-call model usage, written by the model gateway (handoff §5 `llm_usage`, §12).

Token counts are what the provider reported; a missing count stays NULL rather than being
estimated. `usage_source` says whether the counts were reported by the provider, estimated (by
LiteLLM, for providers that do not report usage: OD-21) or not reported at all. Each call links
the pricing snapshot in force for its model (`model_price_id`, ADR-0039; NULL when no price is
known). Costs are computed from that price when the call is recorded (`app.llm.costing`,
ADR-0040), all together with the currency, pricing version and `cost_basis`; a call without a
price or without reported counts stays unpriced (all NULL).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class UsageSource(StrEnum):
    REPORTED = "reported"  # the provider reported the counts
    ESTIMATED = "estimated"  # counts present, but the provider does not report usage (OD-21)
    NOT_REPORTED = "not_reported"  # no counts at all


class CostBasis(StrEnum):
    # What a computed cost is (ADR-0040); never present an estimate as an actual charge.
    ACTUAL = "actual"  # provider-reported counts at a real price
    ESTIMATED = "estimated"  # estimated counts (OD-21) at a real price
    MOCK = "mock"  # the mock provider's price: never a real charge


class LlmCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # the provider call raised or returned an error
    INVALID_OUTPUT = "INVALID_OUTPUT"  # a response arrived but failed schema validation


_COSTS = ("input_cost", "output_cost", "total_cost", "currency", "pricing_version")
_ALL_NULL = " AND ".join(f"{c} IS NULL" for c in _COSTS)
_ALL_SET = " AND ".join(f"{c} IS NOT NULL" for c in _COSTS)


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_llm_usage_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_llm_usage_task",
        ),
        CheckConstraint("task_id IS NULL OR workflow_id IS NOT NULL", name="task_needs_workflow"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (cached_input_tokens IS NULL OR cached_input_tokens >= 0)",
            name="tokens_non_negative",
        ),
        CheckConstraint(f"({_ALL_NULL}) OR ({_ALL_SET})", name="costs_all_or_nothing"),
        CheckConstraint("(cost_basis IS NULL) = (total_cost IS NULL)", name="cost_has_basis"),
        CheckConstraint(
            "(status = 'SUCCEEDED') = (error_code IS NULL)", name="error_code_iff_unsuccessful"
        ),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        Index("ix_llm_usage_workspace_id_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE"), index=True
    )
    # Why the call was made (e.g. "prompt_analysis"); a slug chosen by the caller.
    purpose: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(50))
    # The exact model identifier the provider was asked for.
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[LlmCallStatus] = mapped_column(str_enum(LlmCallStatus, "status"))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cached_input_tokens: Mapped[int | None]
    # Provider-specific usage fields, as reported.
    other_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    input_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    output_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str | None] = mapped_column(String(3))
    pricing_version: Mapped[str | None] = mapped_column(String(100))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    usage_source: Mapped[UsageSource] = mapped_column(str_enum(UsageSource, "usage_source"))
    # Set with the costs (ADR-0040): actual, estimated or mock.
    cost_basis: Mapped[CostBasis | None] = mapped_column(str_enum(CostBasis, "cost_basis"))
    # The price in force for this provider and model when the call was recorded (ADR-0039).
    model_price_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_prices.id", ondelete="RESTRICT"), index=True
    )
    # A short code only; never provider error text, prompts or outputs.
    error_code: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = created_at_column()
