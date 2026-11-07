"""Idempotency for externally initiated operations (ADR-0006).

Usage, inside ONE transaction owned by the calling service:

    outcome = await idempotency.begin(session, workspace_id=..., operation="workflows.create",
                                      key=header_value, payload=request.model_dump(mode="json"))
    if isinstance(outcome, StoredResponse):
        return outcome                      # replay; the operation does not run again
    result = ...                            # perform the business operation with `session`
    await idempotency.complete(session, outcome, status_code=201, body=response_body)
    await session.commit()                  # business changes and the record commit together

Unexpected errors roll the transaction back, removing the key claim too, so the client may
retry. Expected client errors (4xx) may be recorded with `fail()` and are then replayed.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.sensitive_data import find_sensitive_keys
from app.models import IdempotencyStatus
from app.repositories import idempotency as repository

# Visible ASCII only: no whitespace or control characters.
_VALID_KEY = re.compile(r"[\x21-\x7e]{1,255}")
_MAX_OPERATION_LENGTH = 100


class InvalidIdempotencyKeyError(AppError):
    status_code = 400
    code = "INVALID_IDEMPOTENCY_KEY"
    message = "Idempotency key must be 1-255 visible ASCII characters."


class IdempotencyKeyReusedError(AppError):
    status_code = 409
    code = "IDEMPOTENCY_KEY_REUSED"
    message = "This idempotency key was already used with a different request."


class IdempotencyRequestInProgressError(AppError):
    status_code = 409
    code = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    message = "A request with this idempotency key is still being processed."


class UnsafeIdempotentResponseError(ValueError):
    """Programming error: a response containing credentials must never be cached."""


@dataclass(frozen=True)
class NewRequest:
    """The key was claimed: run the operation, then call complete() or fail()."""

    record_id: uuid.UUID
    workspace_id: uuid.UUID


@dataclass(frozen=True)
class StoredResponse:
    """The operation already ran: return this response without running it again."""

    status_code: int
    body: Any


def request_fingerprint(payload: Any) -> str:
    """SHA-256 of the canonical JSON form. Key order and whitespace do not matter."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyService:
    def __init__(self, ttl: timedelta) -> None:
        self._ttl = ttl

    async def begin(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        operation: str,
        key: str,
        payload: Any,
    ) -> NewRequest | StoredResponse:
        if not _VALID_KEY.fullmatch(key):
            raise InvalidIdempotencyKeyError()
        if not 0 < len(operation) <= _MAX_OPERATION_LENGTH:
            raise ValueError("operation must be 1-100 characters")
        request_hash = request_fingerprint(payload)

        # Second pass only if the conflicting row disappeared between our statements
        # (it expired and was purged). Bounded: never loops indefinitely.
        for _ in range(2):
            await repository.delete_expired_key(session, workspace_id, operation, key)
            record_id = await repository.insert_processing(
                session,
                workspace_id=workspace_id,
                operation=operation,
                key=key,
                request_hash=request_hash,
                ttl=self._ttl,
            )
            if record_id is not None:
                return NewRequest(record_id=record_id, workspace_id=workspace_id)

            existing = await repository.get_by_key(session, workspace_id, operation, key)
            if existing is None:
                continue
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError()
            if existing.status == IdempotencyStatus.PROCESSING or (
                existing.response_status_code is None
            ):
                raise IdempotencyRequestInProgressError()
            return StoredResponse(existing.response_status_code, existing.response_body)
        raise IdempotencyRequestInProgressError()

    async def complete(
        self, session: AsyncSession, request: NewRequest, *, status_code: int, body: Any
    ) -> None:
        if not 200 <= status_code < 400:
            raise ValueError("complete() records successful responses (2xx/3xx) only")
        await self._finish(session, request, IdempotencyStatus.COMPLETED, status_code, body)

    async def fail(
        self, session: AsyncSession, request: NewRequest, *, status_code: int, body: Any
    ) -> None:
        """Records an expected client error so retries replay it. 5xx must roll back instead."""
        if not 400 <= status_code < 500:
            raise ValueError("fail() records client errors (4xx) only; roll back on 5xx")
        await self._finish(session, request, IdempotencyStatus.FAILED, status_code, body)

    async def purge_expired(self, session: AsyncSession, batch_size: int = 1000) -> int:
        """Deletes up to `batch_size` expired records. The caller commits."""
        return await repository.purge_expired(session, batch_size)

    async def _finish(
        self,
        session: AsyncSession,
        request: NewRequest,
        status: IdempotencyStatus,
        status_code: int,
        body: Any,
    ) -> None:
        sensitive = find_sensitive_keys(body)
        if sensitive:
            raise UnsafeIdempotentResponseError(
                f"refusing to cache a response with sensitive fields: {', '.join(sensitive)}"
            )
        request_fingerprint(body)  # rejects values that are not plain JSON
        finished = await repository.finish(
            session,
            record_id=request.record_id,
            workspace_id=request.workspace_id,
            status=status,
            response_status_code=status_code,
            response_body=body,
        )
        if not finished:
            raise RuntimeError("idempotency record is missing or already finished")
