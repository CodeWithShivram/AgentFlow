"""Model usage records (handoff §12). Written only by the model gateway.

Each record is committed in its own short transaction, so a call is accounted for even when
the caller's unit of work later rolls back: the provider has already done (and billed) it.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.llm.types import CallContext, ProviderUsage
from app.models import LlmCallStatus, LlmUsage


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
        session.add(row)
        session.commit()
    return row.id
