"""Async Redis client. Redis is transport/cache only, never the authoritative state store."""

from redis.asyncio import Redis
from starlette.requests import HTTPConnection

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    # Connections are opened lazily, so an unavailable Redis never blocks startup.
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password.get_secret_value(),
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        client_name=settings.service_name,
    )


def get_redis(connection: HTTPConnection) -> Redis:
    client: Redis = connection.app.state.redis
    return client
