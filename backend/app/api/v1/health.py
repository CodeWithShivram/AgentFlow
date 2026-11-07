from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


@router.get("/live")
def live() -> LivenessResponse:
    """Process is up. Dependency checks belong to /health/ready (Step 1.3)."""
    return LivenessResponse(status="ok")
