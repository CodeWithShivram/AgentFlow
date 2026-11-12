"""Shared request dependencies: database session, settings, authentication and authorization.

Authentication reads the access cookie, verifies the JWT, then loads the session and user from
the database, so a revoked session stops working immediately. Unsafe methods additionally
require the session's CSRF token in X-CSRF-Token.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request, WebSocket, WebSocketException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.core.config import Settings, get_app_settings
from app.core.cookies import CSRF_HEADER, cookie_names
from app.core.origin import UNSAFE_METHODS
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.core.tokens import InvalidAccessTokenError, decode_access_token
from app.db.session import get_db_session, set_session_user
from app.models import AuthSession, User, WorkspaceRole
from app.repositories import auth_sessions as sessions_repository
from app.services.auth import AuthenticationRequiredError, AuthService, verify_csrf
from app.services.authorization import WorkspaceAccess, authorize_workspace

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: uuid.UUID

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


async def _authenticate(
    connection: HTTPConnection, session: AsyncSession, settings: Settings
) -> tuple[Principal, AuthSession]:
    token = connection.cookies.get(cookie_names(settings).access)
    if not token:
        raise AuthenticationRequiredError()
    try:
        claims = decode_access_token(settings, token)
    except InvalidAccessTokenError as exc:
        raise AuthenticationRequiredError() from exc
    found = await sessions_repository.get_active_session(
        session, session_id=claims.session_id, user_id=claims.user_id, now=datetime.now(UTC)
    )
    if found is None:
        raise AuthenticationRequiredError()
    auth_session, user = found
    # Everything after authentication runs under row-level security for this user (ADR-0012).
    await set_session_user(session, user.id)
    return Principal(user=user, session_id=auth_session.id), auth_session


async def get_current_principal(
    request: Request, session: DbSession, settings: SettingsDep
) -> Principal:
    principal, auth_session = await _authenticate(request, session, settings)
    if request.method in UNSAFE_METHODS:
        verify_csrf(request.headers.get(CSRF_HEADER), auth_session)
    return principal


async def get_websocket_principal(
    websocket: WebSocket, session: DbSession, settings: SettingsDep
) -> Principal:
    """Cookie authentication for WebSockets. OriginCheckMiddleware has validated the handshake."""
    try:
        principal, _ = await _authenticate(websocket, session, settings)
    except AuthenticationRequiredError as exc:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from exc
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def workspace_role(
    minimum: WorkspaceRole,
) -> Callable[[uuid.UUID, Principal, AsyncSession], Awaitable[WorkspaceAccess]]:
    """Dependency factory: authorizes the path's `workspace_id` for the current user."""

    async def dependency(
        workspace_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
    ) -> WorkspaceAccess:
        return await authorize_workspace(
            session, user_id=principal.user_id, workspace_id=workspace_id, minimum=minimum
        )

    return dependency


def client_ip(request: Request) -> str:
    # The direct peer. Behind a reverse proxy this must come from a trusted forwarding header,
    # configured explicitly (not implemented in the MVP).
    return request.client.host if request.client else "unknown"


def get_rate_limiter(connection: HTTPConnection, settings: SettingsDep) -> RateLimiter:
    return RateLimiter(get_redis(connection), namespace=settings.service_name)


def get_auth_service(settings: SettingsDep) -> AuthService:
    return AuthService(settings)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
