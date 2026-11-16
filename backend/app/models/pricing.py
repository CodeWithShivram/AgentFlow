"""Pricing snapshots (handoff §12, ADR-0039): immutable, versioned model prices.

A version is loaded from the curated `app/llm/pricing.json` by `python -m app.llm.pricing load`
and never changed afterwards (triggers refuse updates and deletes), so the price a call was
recorded against stays reproducible. Prices are platform-wide, not workspace data.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk


class PricingVersion(Base):
    __tablename__ = "pricing_versions"
    __table_args__ = (CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # The file's version label; a changed file needs a new label.
    version: Mapped[str] = mapped_column(String(64), unique=True)
    currency: Mapped[str] = mapped_column(String(3))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    # Per provider: {"reports_usage": bool}. A provider not listed is treated as not reporting
    # its own usage, so its token counts are recorded as estimated (OD-21).
    providers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # SHA-256 of the loaded file's canonical content.
    content_sha256: Mapped[str] = mapped_column(String(64))
    loaded_at: Mapped[datetime] = created_at_column()


class ModelPrice(Base):
    __tablename__ = "model_prices"
    __table_args__ = (
        UniqueConstraint("pricing_version_id", "provider", "model"),
        CheckConstraint(
            "input_price_per_million >= 0 AND output_price_per_million >= 0 "
            "AND (cached_input_price_per_million IS NULL OR cached_input_price_per_million >= 0)",
            name="prices_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    pricing_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pricing_versions.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50))
    # The exact model identifier, as the gateway records it in `llm_usage.model`.
    model: Mapped[str] = mapped_column(String(200))
    input_price_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    output_price_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    # NULL: the provider has no separate cached-input price.
    cached_input_price_per_million: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    # A price for the mock provider: never a real charge.
    is_mock: Mapped[bool] = mapped_column(default=False)
