"""Pricing snapshot API models (ADR-0039). Prices are per million tokens, as decimal strings."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ModelPriceResponse(BaseModel):
    model: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    cached_input_price_per_million: Decimal | None
    # The mock provider's price: never a real charge.
    is_mock: bool


class ProviderPricing(BaseModel):
    provider: str
    # False: this provider's token counts are estimates (OD-21).
    reports_usage: bool
    models: list[ModelPriceResponse]


class PricingResponse(BaseModel):
    version: str
    currency: str
    effective_from: datetime
    source: str
    loaded_at: datetime
    providers: list[ProviderPricing]
