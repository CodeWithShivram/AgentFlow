"""The platform's model prices (ADR-0039), read-only for every signed-in user."""

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.pricing import PricingResponse
from app.services import pricing as service

router = APIRouter(tags=["pricing"])


@router.get("/pricing")
async def current_pricing(_: CurrentPrincipal, session: DbSession) -> PricingResponse:
    """The price list in force now. Prices are the providers', the same for every workspace."""
    return await service.current_pricing(session)
