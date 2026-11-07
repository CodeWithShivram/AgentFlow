"""Fixed-window rate limiting in Redis.

Counters live only in Redis (transport/cache, never business state). Identifiers such as
emails are hashed before they become keys. If Redis is unavailable the limiter fails CLOSED:
authentication endpoints return 503 rather than run without abuse protection.
"""

import hashlib
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import AppError

logger = logging.getLogger("agentflow.rate_limit")


class RateLimitExceededError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests. Try again later."


class RateLimiterUnavailableError(AppError):
    status_code = 503
    code = "RATE_LIMITER_UNAVAILABLE"
    message = "The service is temporarily unavailable. Try again later."


class RateLimiter:
    def __init__(self, redis: Redis, namespace: str) -> None:
        self._redis = redis
        self._namespace = namespace

    async def hit(
        self, bucket: str, identifier: str, *, limit: int, window_seconds: int = 60
    ) -> None:
        """Counts one request; raises RateLimitExceededError once `limit` is passed."""
        now = time.time()
        window = int(now // window_seconds)
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
        key = f"{self._namespace}:ratelimit:{bucket}:{digest}:{window}"
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window_seconds + 5)
                count, _ = await pipeline.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            logger.error("rate limiter unavailable", extra={"error_type": type(exc).__name__})
            raise RateLimiterUnavailableError() from exc
        if int(count) > limit:
            retry_after = max(1, window_seconds - int(now % window_seconds))
            logger.warning("rate limit exceeded", extra={"bucket": bucket})
            raise RateLimitExceededError(headers={"Retry-After": str(retry_after)})
