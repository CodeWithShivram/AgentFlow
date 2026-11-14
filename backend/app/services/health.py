"""Dependency health checks. Results never include hosts, credentials or error text."""

import asyncio
import logging
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.execution import heartbeat

logger = logging.getLogger("agentflow.health")

DependencyStatus = Literal["ok", "unavailable", "lagging"]


async def check_database(engine: AsyncEngine, timeout_seconds: float) -> DependencyStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("database health check failed", extra={"error_type": type(exc).__name__})
        return "unavailable"
    return "ok"


async def check_redis(client: Redis, timeout_seconds: float) -> DependencyStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await client.ping()
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning("redis health check failed", extra={"error_type": type(exc).__name__})
        return "unavailable"
    return "ok"


async def check_workers(client: Redis, timeout_seconds: float) -> DependencyStatus:
    """The outbox relay's heartbeat (ADR-0027): recent, and no event waiting too long."""
    try:
        async with asyncio.timeout(timeout_seconds):
            raw = await client.get(heartbeat.KEY)
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning("worker health check failed", extra={"error_type": type(exc).__name__})
        return "unavailable"
    return heartbeat.status(raw)


async def check_readiness(
    engine: AsyncEngine, redis: Redis, timeout_seconds: float
) -> dict[str, DependencyStatus]:
    database, redis_status, workers = await asyncio.gather(
        check_database(engine, timeout_seconds),
        check_redis(redis, timeout_seconds),
        check_workers(redis, timeout_seconds),
    )
    return {"database": database, "redis": redis_status, "workers": workers}
