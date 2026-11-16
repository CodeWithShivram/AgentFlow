"""Reads the platform's pricing snapshots (ADR-0039). Prices are written only by the
administrator's loader (`python -m app.llm.pricing load`); the API role may only read them."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import ModelPrice, PricingVersion
from app.schemas.pricing import ModelPriceResponse, PricingResponse, ProviderPricing


class PricingNotLoadedError(AppError):
    status_code = 404
    code = "PRICING_NOT_LOADED"
    message = "No price list is in force yet."


async def current_pricing(session: AsyncSession) -> PricingResponse:
    """The snapshot in force now: the latest effective one, the latest loaded on a tie."""
    version = (
        await session.execute(
            select(PricingVersion)
            .where(PricingVersion.effective_from <= datetime.now(UTC))
            .order_by(PricingVersion.effective_from.desc(), PricingVersion.loaded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        raise PricingNotLoadedError()
    prices = (
        await session.execute(
            select(ModelPrice)
            .where(ModelPrice.pricing_version_id == version.id)
            .order_by(ModelPrice.provider, ModelPrice.model)
        )
    ).scalars()
    by_provider: dict[str, list[ModelPriceResponse]] = {name: [] for name in version.providers}
    for price in prices:
        by_provider.setdefault(price.provider, []).append(
            ModelPriceResponse(
                model=price.model,
                input_price_per_million=price.input_price_per_million,
                output_price_per_million=price.output_price_per_million,
                cached_input_price_per_million=price.cached_input_price_per_million,
                is_mock=price.is_mock,
            )
        )
    return PricingResponse(
        version=version.version,
        currency=version.currency,
        effective_from=version.effective_from,
        source=version.source,
        loaded_at=version.loaded_at,
        providers=[
            ProviderPricing(
                provider=name,
                reports_usage=bool((version.providers.get(name) or {}).get("reports_usage")),
                models=models,
            )
            for name, models in sorted(by_provider.items())
        ],
    )
