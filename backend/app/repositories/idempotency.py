"""Queries for idempotency records. Every statement is scoped by workspace_id."""

import uuid
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyKey, IdempotencyStatus

_UNIQUE_KEY = "uq_idempotency_keys_workspace_id_operation_idempotency_key"


def _key_filter(workspace_id: uuid.UUID, operation: str, key: str) -> tuple[Any, ...]:
    return (
        IdempotencyKey.workspace_id == workspace_id,
        IdempotencyKey.operation == operation,
        IdempotencyKey.idempotency_key == key,
    )


async def delete_expired_key(
    session: AsyncSession, workspace_id: uuid.UUID, operation: str, key: str
) -> None:
    await session.execute(
        delete(IdempotencyKey).where(
            *_key_filter(workspace_id, operation, key), IdempotencyKey.expires_at <= func.now()
        )
    )


async def insert_processing(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    operation: str,
    key: str,
    request_hash: str,
    ttl: timedelta,
) -> uuid.UUID | None:
    """Claims the key. Returns None if a committed row already holds it.

    If another open transaction holds the same key, PostgreSQL blocks this INSERT until that
    transaction ends: on commit the INSERT does nothing, on rollback it proceeds.
    """
    statement = (
        insert(IdempotencyKey)
        .values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            operation=operation,
            idempotency_key=key,
            request_hash=request_hash,
            status=IdempotencyStatus.PROCESSING,
            expires_at=func.now() + ttl,
        )
        .on_conflict_do_nothing(constraint=_UNIQUE_KEY)
        .returning(IdempotencyKey.id)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def get_by_key(
    session: AsyncSession, workspace_id: uuid.UUID, operation: str, key: str
) -> IdempotencyKey | None:
    statement = (
        select(IdempotencyKey)
        .where(*_key_filter(workspace_id, operation, key))
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def finish(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    workspace_id: uuid.UUID,
    status: IdempotencyStatus,
    response_status_code: int,
    response_body: Any,
) -> bool:
    """Moves a PROCESSING record to its final state. False if no such record."""
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(IdempotencyKey)
            .where(
                IdempotencyKey.id == record_id,
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.status == IdempotencyStatus.PROCESSING,
            )
            .values(
                status=status,
                response_status_code=response_status_code,
                response_body=response_body,
                updated_at=func.now(),
            )
        ),
    )
    return result.rowcount == 1


async def purge_expired(session: AsyncSession, batch_size: int) -> int:
    """Deletes up to `batch_size` expired records; skips rows locked by other transactions."""
    expired_ids = (
        select(IdempotencyKey.id)
        .where(IdempotencyKey.expires_at <= func.now())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = cast(
        CursorResult[Any],
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id.in_(expired_ids))),
    )
    return result.rowcount
