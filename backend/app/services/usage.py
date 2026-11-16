"""Model usage and tool invocation records (handoff §11, §12). Written only by the model and tool
gateways.

Each record is committed in its own short transaction, so a call is accounted for even when
the caller's unit of work later rolls back: the provider has already done (and billed) it.
A model call is recorded with where its token counts came from and the price snapshot in force
for its provider and model at that moment (ADR-0039), so its cost (Step 7.2) is reproducible.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.llm import pricing
from app.llm.types import CallContext, ProviderUsage
from app.models import LlmCallStatus, LlmUsage, ModelPrice, ToolInvocation, UsageSource
from app.tools.gateway import InvocationRecord


@dataclass(frozen=True)
class CallRecord:
    context: CallContext
    purpose: str
    provider: str
    model: str
    status: LlmCallStatus
    usage: ProviderUsage | None
    provider_request_id: str | None
    error_code: str | None
    latency_ms: int


def usage_source(usage: ProviderUsage | None, *, provider_reports_usage: bool) -> UsageSource:
    """Reported only when the provider reports its own usage; counts LiteLLM filled in for a
    provider that does not are estimates (OD-21), never labelled actual."""
    if usage is None or (usage.input_tokens is None and usage.output_tokens is None):
        return UsageSource.NOT_REPORTED
    return UsageSource.REPORTED if provider_reports_usage else UsageSource.ESTIMATED


def _price_in_force(session: Session, provider: str, model: str) -> tuple[uuid.UUID | None, bool]:
    version = pricing.active_version(session, datetime.now(UTC))
    if version is None:
        return None, False
    price_id = session.execute(
        select(ModelPrice.id).where(
            ModelPrice.pricing_version_id == version.id,
            ModelPrice.provider == provider,
            ModelPrice.model == model,
        )
    ).scalar_one_or_none()
    return price_id, pricing.reports_usage(version, provider)


def record_model_call(factory: sessionmaker[Session], record: CallRecord) -> uuid.UUID:
    usage = record.usage
    row = LlmUsage(
        workspace_id=record.context.workspace_id,
        workflow_id=record.context.workflow_id,
        task_id=record.context.task_id,
        execution_id=record.context.execution_id,
        purpose=record.purpose,
        provider=record.provider,
        model=record.model,
        status=record.status,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cached_input_tokens=usage.cached_input_tokens if usage else None,
        other_usage=dict(usage.other) if usage else {},
        provider_request_id=record.provider_request_id,
        error_code=record.error_code,
        latency_ms=record.latency_ms,
    )
    with session_scope(factory) as session:
        price_id, reports = _price_in_force(session, record.provider, record.model)
        row.model_price_id = price_id
        row.usage_source = usage_source(usage, provider_reports_usage=reports)
        session.add(row)
        session.commit()
    return row.id


def record_tool_invocation(factory: sessionmaker[Session], record: InvocationRecord) -> uuid.UUID:
    """One tool gateway call (ADR-0019), committed in its own transaction like model usage."""
    row = ToolInvocation(
        workspace_id=record.context.workspace_id,
        workflow_id=record.context.workflow_id,
        task_id=record.context.task_id,
        execution_id=record.context.execution_id,
        tool_name=record.tool_name,
        status=record.status,
        error_code=record.error_code,
        duration_ms=record.duration_ms,
        input_hash=record.input_hash,
    )
    with session_scope(factory) as session:
        session.add(row)
        session.commit()
    return row.id
