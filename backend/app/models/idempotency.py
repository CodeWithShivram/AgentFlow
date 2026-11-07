"""Idempotency records for externally initiated operations (ADR-0006, OD-4)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk


class IdempotencyStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("workspace_id", "operation", "idempotency_key"),
        CheckConstraint("char_length(request_hash) = 64", name="request_hash_sha256"),
        CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name="response_status_code_range",
        ),
        CheckConstraint(
            "status = 'PROCESSING' OR response_status_code IS NOT NULL",
            name="finished_has_response",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    # SHA-256 hex of the canonical request payload; detects key reuse with a different body.
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[IdempotencyStatus] = mapped_column(
        str_enum(IdempotencyStatus, "status"), default=IdempotencyStatus.PROCESSING
    )
    response_status_code: Mapped[int | None]
    # Replayed verbatim. Never contains credentials, tokens or secrets (enforced on write).
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
