"""Registration, login, refresh-token rotation and logout (ADR-0007).

Each public method owns one database transaction and commits it.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.passwords import hash_password, needs_rehash, spend_verification_time, verify_password
from app.core.tokens import create_access_token, hash_token, new_opaque_token, token_matches
from app.db.session import set_session_user
from app.models import (
    AuthSession,
    RefreshToken,
    SessionRevocationReason,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.repositories import auth_sessions as sessions_repository
from app.repositories import users as users_repository

logger = logging.getLogger("agentflow.auth")

_EMAIL_UNIQUE_INDEX = "uq_users_email_lower"


class AuthenticationRequiredError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    message = "Authentication required."


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "Invalid email or password."


class EmailAlreadyRegisteredError(AppError):
    status_code = 409
    code = "EMAIL_ALREADY_REGISTERED"
    message = "An account with this email already exists."


class InvalidRefreshTokenError(AppError):
    status_code = 401
    code = "INVALID_REFRESH_TOKEN"
    message = "Your session has expired. Please sign in again."


class RefreshTokenReusedError(AppError):
    status_code = 401
    code = "REFRESH_TOKEN_REUSED"
    message = "Your session was ended for security reasons. Please sign in again."


class CsrfTokenInvalidError(AppError):
    status_code = 403
    code = "CSRF_TOKEN_INVALID"
    message = "Missing or invalid CSRF token."


@dataclass(frozen=True)
class IssuedTokens:
    """Raw tokens for the response cookies. Never logged, never returned in a body."""

    user: User
    session_id: uuid.UUID
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_expires_at: datetime
    # Only set when a session starts; refreshes keep the existing CSRF cookie.
    csrf_token: str | None


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def verify_csrf(header_value: str | None, auth_session: AuthSession) -> None:
    if not token_matches(header_value, auth_session.csrf_token_hash):
        raise CsrfTokenInvalidError()


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def register(
        self, session: AsyncSession, *, email: str, password: str, workspace_name: str
    ) -> IssuedTokens:
        """Creates user, personal workspace, OWNER membership and a session atomically."""
        password_hash = await hash_password(password)
        user = User(email=email.lower(), password_hash=password_hash)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if _constraint_name(exc) == _EMAIL_UNIQUE_INDEX:
                raise EmailAlreadyRegisteredError() from exc
            raise
        # The new user owns the workspace; row-level security checks that (ADR-0012).
        await set_session_user(session, user.id)
        workspace = Workspace(owner_id=user.id, name=workspace_name)
        session.add(workspace)
        await session.flush()
        session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
        )
        await session.flush()
        issued = await self._start_session(session, user)
        await session.commit()
        logger.info("user registered", extra={"user_id": str(user.id)})
        return issued

    async def login(self, session: AsyncSession, *, email: str, password: str) -> IssuedTokens:
        user = await users_repository.get_by_email(session, email)
        if user is None:
            await spend_verification_time(password)
            logger.warning("login failed", extra={"reason": "unknown_email"})
            raise InvalidCredentialsError()
        if not await verify_password(user.password_hash, password):
            logger.warning(
                "login failed", extra={"reason": "wrong_password", "user_id": str(user.id)}
            )
            raise InvalidCredentialsError()
        if needs_rehash(user.password_hash):
            user.password_hash = await hash_password(password)
        issued = await self._start_session(session, user)
        await session.commit()
        logger.info("login succeeded", extra={"user_id": str(user.id)})
        return issued

    async def refresh(
        self, session: AsyncSession, *, refresh_token: str | None, csrf_header: str | None
    ) -> IssuedTokens:
        """Rotates the refresh token. Reusing a spent token revokes the whole session."""
        if not refresh_token:
            raise InvalidRefreshTokenError()
        found = await sessions_repository.lock_refresh_token(session, hash_token(refresh_token))
        if found is None:
            raise InvalidRefreshTokenError()
        token, auth_session = found
        now = datetime.now(UTC)
        if auth_session.revoked_at is not None or auth_session.expires_at <= now:
            raise InvalidRefreshTokenError()
        if token.used_at is not None:
            # A spent token came back: it was copied. Revoke the family before anything else.
            self._revoke(auth_session, SessionRevocationReason.REFRESH_TOKEN_REUSE, now)
            await session.commit()
            logger.warning(
                "refresh token reuse detected; session revoked",
                extra={"session_id": str(auth_session.id), "user_id": str(auth_session.user_id)},
            )
            raise RefreshTokenReusedError()
        verify_csrf(csrf_header, auth_session)
        if token.expires_at <= now:
            raise InvalidRefreshTokenError()
        user = await users_repository.get_by_id(session, auth_session.user_id)
        if user is None:
            raise InvalidRefreshTokenError()
        token.used_at = now
        issued = await self._issue_tokens(session, user, auth_session, now, csrf_token=None)
        await session.commit()
        return issued

    async def logout(
        self,
        session: AsyncSession,
        *,
        refresh_token: str | None,
        access_session_id: uuid.UUID | None,
        csrf_header: str | None,
    ) -> None:
        """Revokes the current session (and so every token in its family). Idempotent."""
        auth_session: AuthSession | None = None
        if refresh_token:
            found = await sessions_repository.lock_refresh_token(session, hash_token(refresh_token))
            auth_session = found[1] if found else None
        if auth_session is None and access_session_id is not None:
            auth_session = await sessions_repository.lock_session(session, access_session_id)
        if auth_session is None:
            return
        verify_csrf(csrf_header, auth_session)
        if auth_session.revoked_at is None:
            self._revoke(auth_session, SessionRevocationReason.LOGOUT, datetime.now(UTC))
            await session.commit()
            logger.info("logout", extra={"session_id": str(auth_session.id)})

    async def _start_session(self, session: AsyncSession, user: User) -> IssuedTokens:
        now = datetime.now(UTC)
        csrf_token = new_opaque_token()
        auth_session = AuthSession(
            id=uuid.uuid4(),
            user_id=user.id,
            csrf_token_hash=hash_token(csrf_token),
            expires_at=now + timedelta(seconds=self._settings.auth_session_max_age_seconds),
        )
        session.add(auth_session)
        await session.flush()
        return await self._issue_tokens(session, user, auth_session, now, csrf_token=csrf_token)

    async def _issue_tokens(
        self,
        session: AsyncSession,
        user: User,
        auth_session: AuthSession,
        now: datetime,
        *,
        csrf_token: str | None,
    ) -> IssuedTokens:
        refresh_token = new_opaque_token()
        refresh_expires_at = min(
            now + timedelta(seconds=self._settings.auth_refresh_token_ttl_seconds),
            auth_session.expires_at,
        )
        session.add(
            RefreshToken(
                session_id=auth_session.id,
                token_hash=hash_token(refresh_token),
                expires_at=refresh_expires_at,
            )
        )
        await session.flush()
        access = create_access_token(
            self._settings, user_id=user.id, session_id=auth_session.id, now=now
        )
        return IssuedTokens(
            user=user,
            session_id=auth_session.id,
            access_token=access.token,
            access_expires_at=access.expires_at,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires_at,
            session_expires_at=auth_session.expires_at,
            csrf_token=csrf_token,
        )

    @staticmethod
    def _revoke(auth_session: AuthSession, reason: SessionRevocationReason, now: datetime) -> None:
        auth_session.revoked_at = now
        auth_session.revoked_reason = reason
