"""Request ID correlation: validation, context propagation and ASGI middleware."""

import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
# Client IDs are accepted only if short and free of characters that could inject into logs/headers.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("agentflow.request")


def get_request_id() -> str | None:
    return _request_id.get()


def resolve_request_id(client_value: str | None) -> str:
    if client_value is not None and _SAFE_REQUEST_ID.fullmatch(client_value):
        return client_value
    return str(uuid.uuid4())


class RequestIdMiddleware:
    """Assigns a request ID, exposes it to logs and handlers, and returns it in the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        # Also stored on request.state: the 500 handler runs outside this middleware.
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            _request_id.reset(token)
