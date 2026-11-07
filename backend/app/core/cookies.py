"""Authentication cookies. Tokens travel only in cookies, never in response bodies.

- access:  HttpOnly, SameSite=Lax, Path=/, host-only. `__Host-` prefix when Secure.
- refresh: HttpOnly, SameSite=Strict, Path=/api/v1/auth (only sent to refresh/logout).
           `__Secure-` prefix when Secure (`__Host-` requires Path=/).
- csrf:    readable by the frontend (not HttpOnly) so it can echo it in X-CSRF-Token.
"""

from dataclasses import dataclass
from typing import Literal

from starlette.responses import Response

from app.core.config import Settings

CSRF_HEADER = "X-CSRF-Token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


@dataclass(frozen=True)
class CookieNames:
    access: str
    refresh: str
    csrf: str


def cookie_names(settings: Settings) -> CookieNames:
    if settings.cookies_secure:
        return CookieNames(
            access="__Host-agentflow_access",
            refresh="__Secure-agentflow_refresh",
            csrf="__Host-agentflow_csrf",
        )
    return CookieNames(
        access="agentflow_access", refresh="agentflow_refresh", csrf="agentflow_csrf"
    )


def _set(
    response: Response,
    settings: Settings,
    name: str,
    value: str,
    *,
    max_age: int,
    path: str,
    httponly: bool,
    samesite: Literal["lax", "strict"],
) -> None:
    # No Domain attribute: cookies stay host-only.
    response.set_cookie(
        name,
        value,
        max_age=max(max_age, 0),
        path=path,
        secure=settings.cookies_secure,
        httponly=httponly,
        samesite=samesite,
    )


def set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    access_token: str,
    refresh_token: str,
    refresh_max_age: int,
    csrf_token: str | None,
    csrf_max_age: int,
) -> None:
    """Sets access and refresh cookies, plus the CSRF cookie when a new one was issued."""
    names = cookie_names(settings)
    _set(
        response,
        settings,
        names.access,
        access_token,
        max_age=settings.auth_access_token_ttl_seconds,
        path="/",
        httponly=True,
        samesite="lax",
    )
    _set(
        response,
        settings,
        names.refresh,
        refresh_token,
        max_age=refresh_max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
    )
    if csrf_token is not None:
        _set(
            response,
            settings,
            names.csrf,
            csrf_token,
            max_age=csrf_max_age,
            path="/",
            httponly=False,
            samesite="lax",
        )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    names = cookie_names(settings)
    for name, path in (
        (names.access, "/"),
        (names.refresh, REFRESH_COOKIE_PATH),
        (names.csrf, "/"),
    ):
        response.delete_cookie(
            name, path=path, secure=settings.cookies_secure, httponly=name != names.csrf
        )
