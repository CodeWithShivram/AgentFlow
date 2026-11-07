"""Authentication endpoints. Tokens are only ever set as cookies (ADR-0007)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    AuthServiceDep,
    CurrentPrincipal,
    DbSession,
    RateLimiterDep,
    SettingsDep,
    client_ip,
)
from app.core.config import Settings
from app.core.cookies import CSRF_HEADER, clear_session_cookies, cookie_names, set_session_cookies
from app.core.errors import error_response
from app.core.tokens import InvalidAccessTokenError, decode_access_token
from app.models import User
from app.repositories import workspaces as workspaces_repository
from app.schemas.auth import (
    LoginRequest,
    MembershipResponse,
    MeResponse,
    RegisterRequest,
    SessionResponse,
    UserResponse,
)
from app.services.auth import InvalidRefreshTokenError, IssuedTokens, RefreshTokenReusedError

router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_WORKSPACE_NAME = "Personal workspace"
# The session payload lists up to this many memberships; GET /workspaces pages through all.
_MEMBERSHIPS_IN_SESSION = 100


async def _me(session: DbSession, user: User) -> MeResponse:
    rows, _ = await workspaces_repository.list_for_user(
        session, user_id=user.id, limit=_MEMBERSHIPS_IN_SESSION, offset=0
    )
    return MeResponse(
        user=UserResponse(id=user.id, email=user.email, created_at=user.created_at),
        memberships=[
            MembershipResponse(workspace_id=w.id, workspace_name=w.name, role=role)
            for w, role in rows
        ],
    )


async def _session_response(
    session: DbSession, response: Response, settings: Settings, issued: IssuedTokens
) -> SessionResponse:
    now = datetime.now(UTC)
    set_session_cookies(
        response,
        settings,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        refresh_max_age=int((issued.refresh_expires_at - now).total_seconds()),
        csrf_token=issued.csrf_token,
        csrf_max_age=int((issued.session_expires_at - now).total_seconds()),
    )
    me = await _me(session, issued.user)
    return SessionResponse(
        **me.model_dump(),
        access_expires_at=issued.access_expires_at,
        session_expires_at=issued.session_expires_at,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    auth: AuthServiceDep,
) -> SessionResponse:
    """Creates the account, a personal workspace with the caller as OWNER, and a session."""
    await limiter.hit("register:ip", client_ip(request), limit=settings.auth_requests_per_minute)
    issued = await auth.register(
        session,
        email=body.email,
        password=body.password.get_secret_value(),
        workspace_name=body.workspace_name or DEFAULT_WORKSPACE_NAME,
    )
    return await _session_response(session, response, settings, issued)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    auth: AuthServiceDep,
) -> SessionResponse:
    limit = settings.auth_login_attempts_per_minute
    await limiter.hit("login:ip", client_ip(request), limit=limit)
    await limiter.hit("login:email", body.email, limit=limit)
    issued = await auth.login(session, email=body.email, password=body.password.get_secret_value())
    return await _session_response(session, response, settings, issued)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    auth: AuthServiceDep,
) -> Any:
    """Rotates the refresh token (cookie) and issues a new access token."""
    await limiter.hit("refresh:ip", client_ip(request), limit=settings.auth_requests_per_minute)
    try:
        issued = await auth.refresh(
            session,
            refresh_token=request.cookies.get(cookie_names(settings).refresh),
            csrf_header=request.headers.get(CSRF_HEADER),
        )
    except (InvalidRefreshTokenError, RefreshTokenReusedError) as exc:
        # A dead session's cookies are useless; clear them along with the error.
        failure = error_response(request, exc.status_code, exc.code, exc.message)
        clear_session_cookies(failure, settings)
        return failure
    return await _session_response(session, response, settings, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    auth: AuthServiceDep,
) -> Response:
    """Revokes the session and every token in it, then clears the cookies. Idempotent."""
    await limiter.hit("logout:ip", client_ip(request), limit=settings.auth_requests_per_minute)
    names = cookie_names(settings)
    access_session_id: uuid.UUID | None = None
    access_token = request.cookies.get(names.access)
    if access_token:
        try:
            access_session_id = decode_access_token(settings, access_token).session_id
        except InvalidAccessTokenError:
            access_session_id = None
    await auth.logout(
        session,
        refresh_token=request.cookies.get(names.refresh),
        access_session_id=access_session_id,
        csrf_header=request.headers.get(CSRF_HEADER),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response, settings)
    return response


@router.get("/me")
async def me(principal: CurrentPrincipal, session: DbSession) -> MeResponse:
    return await _me(session, principal.user)
