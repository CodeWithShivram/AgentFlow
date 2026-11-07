"""Origin validation for unsafe HTTP methods and WebSocket handshakes (CSRF defence in depth).

Browsers always send Origin on cross-origin and WebSocket requests. Unsafe requests whose
Origin (or, if absent, Referer) is not an allowed origin are rejected before routing.
"""

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import ErrorBody, ErrorResponse
from app.core.request_id import REQUEST_ID_HEADER

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
logger = logging.getLogger("agentflow.security")


def request_origin(headers: Headers) -> str | None:
    origin = headers.get("origin")
    if origin and origin != "null":
        return origin
    referer = headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return None


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        checked = scope["type"] == "websocket" or (
            scope["type"] == "http" and scope["method"] in UNSAFE_METHODS
        )
        if checked:
            origin = request_origin(Headers(scope=scope))
            if origin not in self.allowed_origins:
                logger.warning(
                    "request rejected: origin not allowed",
                    extra={"path": scope["path"], "origin": origin or "<missing>"},
                )
                await self._reject(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "origin not allowed"})
            return
        request_id = scope.get("state", {}).get("request_id")
        body = ErrorResponse(
            error=ErrorBody(
                code="ORIGIN_NOT_ALLOWED",
                message="Request origin is not allowed.",
                request_id=request_id,
            )
        )
        headers = {REQUEST_ID_HEADER: request_id} if request_id else None
        response = JSONResponse(body.model_dump(), status_code=403, headers=headers)
        await response(scope, receive, send)
