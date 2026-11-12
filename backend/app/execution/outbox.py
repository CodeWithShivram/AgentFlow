"""Transactional outbox (ADR-0010).

Writers add rows in the same transaction as the business change. A relay publishes them in
three separate, short steps, never holding a transaction open across a broker call:

1. `claim_batch`   - one transaction: lock due rows with FOR UPDATE SKIP LOCKED (pending rows
                     whose `available_at` has passed, or processing rows whose lease expired),
                     mark them PROCESSING with this relay's lease, count the attempt, commit.
2. publish         - no transaction: hand the message to the broker.
3. `mark_published` / `mark_failed` - one short transaction per row, guarded by the lease owner
                     so a relay whose lease expired cannot overwrite a newer claim.

Delivery is at-least-once: a crash between publish and step 3 republishes after the lease
expires. Consumers deduplicate on the stable event ID (`inbox`).
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import InboxMessage, OutboxEvent, OutboxStatus

TASK_DISPATCH = "task.dispatch"
WORKFLOW_ANALYZE = "workflow.analyze"
_MAX_BACKOFF_SECONDS = 300
_MAX_ERROR_LENGTH = 1000


class _AddsRows(Protocol):
    def add(self, instance: object, /) -> None: ...


@dataclass(frozen=True)
class ClaimedEvent:
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    attempts: int


def enqueue(
    session: _AddsRows,
    event_type: str,
    *,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Adds an outbox row to the caller's transaction. Payloads carry identifiers only."""
    event_id = uuid.uuid4()
    session.add(
        OutboxEvent(
            id=event_id,
            event_type=event_type,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            task_id=task_id,
            payload=payload or {},
            status=OutboxStatus.PENDING,
            attempts=0,
        )
    )
    return event_id


def claim_batch(
    session: Session, *, owner: str, batch_size: int, lease_seconds: int
) -> list[ClaimedEvent]:
    """Step 1. Commits before returning so no lock is held during publication."""
    now = func.now()
    due = (
        select(OutboxEvent.id)
        .where(
            or_(
                and_(OutboxEvent.status == OutboxStatus.PENDING, OutboxEvent.available_at <= now),
                and_(
                    OutboxEvent.status == OutboxStatus.PROCESSING,
                    OutboxEvent.lease_expires_at < now,
                ),
            )
        )
        .order_by(OutboxEvent.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    rows = session.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id.in_(due.scalar_subquery()))
        .values(
            status=OutboxStatus.PROCESSING,
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempts=OutboxEvent.attempts + 1,
            updated_at=now,
        )
        .returning(
            OutboxEvent.id, OutboxEvent.event_type, OutboxEvent.payload, OutboxEvent.attempts
        )
        .execution_options(synchronize_session=False)
    ).all()
    session.commit()
    return [ClaimedEvent(row[0], row[1], row[2], row[3]) for row in rows]


def mark_published(session: Session, event_id: uuid.UUID, *, owner: str) -> bool:
    """Step 3 (success). False if the lease was lost meanwhile; the row is then left alone."""
    result = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == OutboxStatus.PROCESSING,
            OutboxEvent.lease_owner == owner,
        )
        .values(
            status=OutboxStatus.PUBLISHED,
            published_at=func.now(),
            lease_owner=None,
            lease_expires_at=None,
            last_error=None,
            updated_at=func.now(),
        )
        .returning(OutboxEvent.id)
        .execution_options(synchronize_session=False)
    ).first()
    session.commit()
    return result is not None


def mark_failed(
    session: Session,
    event_id: uuid.UUID,
    *,
    owner: str,
    attempts: int,
    max_attempts: int,
    error: str,
) -> bool:
    """Step 3 (failure). Keeps the event: back to PENDING with exponential backoff, or FAILED
    once `max_attempts` is reached. Never deletes it."""
    give_up = attempts >= max_attempts
    backoff = timedelta(seconds=min(2**attempts, _MAX_BACKOFF_SECONDS))
    result = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == OutboxStatus.PROCESSING,
            OutboxEvent.lease_owner == owner,
        )
        .values(
            status=OutboxStatus.FAILED if give_up else OutboxStatus.PENDING,
            available_at=func.now() + backoff,
            lease_owner=None,
            lease_expires_at=None,
            last_error=error[:_MAX_ERROR_LENGTH],
            updated_at=func.now(),
        )
        .returning(OutboxEvent.id)
        .execution_options(synchronize_session=False)
    ).first()
    session.commit()
    return result is not None


def record_consumed(session: Session, *, event_id: uuid.UUID, consumer: str) -> bool:
    """Inbox: True the first time `consumer` sees `event_id` in a committed transaction. Call it
    in the same transaction as the consumer's effects, so a rollback forgets both."""
    inserted = session.execute(
        insert(InboxMessage)
        .values(event_id=event_id, consumer=consumer)
        .on_conflict_do_nothing()
        .returning(InboxMessage.event_id)
    ).first()
    return inserted is not None
