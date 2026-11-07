"""Standard API error shape and centralized exception handlers."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import REQUEST_ID_HEADER

logger = logging.getLogger("agentflow.errors")

_HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details or {})
    )
    response_headers = dict(headers or {})
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(body.model_dump(), status_code=status_code, headers=response_headers)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Registered only for StarletteHTTPException (see register_exception_handlers).
    http_exc = cast(StarletteHTTPException, exc)
    status = http_exc.status_code
    if status >= 500 or not isinstance(http_exc.detail, str):
        message = HTTPStatus(status).phrase
    else:
        message = http_exc.detail
    code = _HTTP_ERROR_CODES.get(status, "SERVER_ERROR" if status >= 500 else "HTTP_ERROR")
    return error_response(request, status, code, message, headers=http_exc.headers)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    # Submitted values ("input") and context are omitted on purpose: they may contain secrets.
    errors = [
        {"loc": list(err["loc"]), "message": err["msg"], "type": err["type"]}
        for err in validation_exc.errors()
    ]
    return error_response(
        request, 422, "VALIDATION_ERROR", "Request validation failed.", {"errors": errors}
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Details go to server logs only. Starlette re-raises after this handler returns,
    # so the exception is not swallowed.
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={
            "request_id": _request_id(request),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
