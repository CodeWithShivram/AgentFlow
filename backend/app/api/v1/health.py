from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_app_settings
from app.core.redis import get_redis
from app.db.session import get_engine
from app.services import health

router = APIRouter(prefix="/health", tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_app_settings)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class DependencyHealthResponse(BaseModel):
    status: health.DependencyStatus


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, health.DependencyStatus]


_UNAVAILABLE: dict[int | str, dict[str, Any]] = {503: {"description": "Dependency unavailable"}}


def _status_code(ok: bool) -> int:
    return 200 if ok else 503


@router.get("/live")
async def live() -> LivenessResponse:
    """Process is running. Does not touch dependencies."""
    return LivenessResponse(status="ok")


@router.get("/ready", responses=_UNAVAILABLE)
async def ready(
    response: Response, settings: SettingsDep, engine: EngineDep, redis: RedisDep
) -> ReadinessResponse:
    """Ready to serve traffic: every dependency is reachable."""
    checks = await health.check_readiness(engine, redis, settings.health_check_timeout_seconds)
    is_ready = all(status == "ok" for status in checks.values())
    response.status_code = _status_code(is_ready)
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)


@router.get("/db", responses=_UNAVAILABLE)
async def database(
    response: Response, settings: SettingsDep, engine: EngineDep
) -> DependencyHealthResponse:
    status = await health.check_database(engine, settings.health_check_timeout_seconds)
    response.status_code = _status_code(status == "ok")
    return DependencyHealthResponse(status=status)


@router.get("/redis", responses=_UNAVAILABLE)
async def redis(
    response: Response, settings: SettingsDep, client: RedisDep
) -> DependencyHealthResponse:
    status = await health.check_redis(client, settings.health_check_timeout_seconds)
    response.status_code = _status_code(status == "ok")
    return DependencyHealthResponse(status=status)
