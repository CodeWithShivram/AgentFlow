"""Access tokens (signed JWT) and opaque single-use tokens (refresh, CSRF, invitations)."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings

# Fixed, never taken from the token header (RFC 8725 section 2.1).
ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - JWT "typ" claim value, not a secret
_CLOCK_SKEW = timedelta(seconds=10)
_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub", "sid", "jti", "typ"]


class InvalidAccessTokenError(Exception):
    """The token is missing, malformed, expired, mis-signed or not an access token."""


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime | None = None,
) -> AccessToken:
    """Signs with the active key. Carries no roles: authorization is always read from the DB."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.auth_access_token_ttl_seconds)
    key_id = settings.auth_jwt_active_key_id
    payload = {
        "iss": settings.auth_jwt_issuer,
        "aud": settings.auth_jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload,
        settings.auth_jwt_signing_keys[key_id].get_secret_value(),
        algorithm=ALGORITHM,
        headers={"kid": key_id},
    )
    return AccessToken(token=token, expires_at=expires_at)


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError("malformed token") from exc
    key_id = header.get("kid")
    key = settings.auth_jwt_signing_keys.get(key_id) if isinstance(key_id, str) else None
    if key is None:
        raise InvalidAccessTokenError("unknown signing key")
    try:
        claims = jwt.decode(
            token,
            key.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.auth_jwt_audience,
            issuer=settings.auth_jwt_issuer,
            leeway=_CLOCK_SKEW,
            options={"require": _REQUIRED_CLAIMS, "strict_aud": True},
        )
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(type(exc).__name__) from exc
    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessTokenError("not an access token")
    try:
        return AccessClaims(user_id=uuid.UUID(claims["sub"]), session_id=uuid.UUID(claims["sid"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidAccessTokenError("malformed subject") from exc


def new_opaque_token() -> str:
    """256 bits from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex. Opaque tokens are high-entropy, so a fast hash is sufficient."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str | None, expected_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(hash_token(token), expected_hash)
