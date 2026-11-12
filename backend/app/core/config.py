"""Centralized application settings, loaded from the environment and the repo-root .env."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL
from starlette.requests import HTTPConnection

REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# Values that must never be used as real secrets in production.
_PLACEHOLDER_SECRETS = frozenset({"change-me", "changeme", "password", "secret"})
# Shorter secrets are not redacted by value, to avoid mangling ordinary log text.
_MIN_REDACTABLE_SECRET_LENGTH = 6
# HS256 keys must carry at least 256 bits; 32 characters is the floor, 64 is recommended.
_MIN_JWT_KEY_LENGTH = 32
# An origin is scheme://host[:port] with no path, query or wildcard.
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Environment = "development"
    service_name: str = Field(default="agentflow-api", min_length=1)
    log_level: LogLevel = "INFO"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    postgres_db: str = Field(min_length=1)
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr = Field(min_length=1)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=60)

    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    # How long an idempotency key is remembered (default 24 h, max 30 days).
    idempotency_key_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)

    # --- Authentication (ADR-0007) ---
    # Key ring as JSON: {"<key id>": "<secret>"}. Tokens are signed with the active key and
    # verified with any key in the ring, so keys can be rotated without logging users out.
    auth_jwt_signing_keys: dict[str, SecretStr]
    auth_jwt_active_key_id: str = Field(min_length=1, max_length=64)
    auth_jwt_issuer: str = Field(default="agentflow-api", min_length=1)
    auth_jwt_audience: str = Field(default="agentflow", min_length=1)
    auth_access_token_ttl_seconds: int = Field(default=900, ge=60, le=900)
    auth_refresh_token_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    auth_session_max_age_seconds: int = Field(default=2_592_000, ge=300, le=7_776_000)
    # None = secure cookies in production only. Production rejects False.
    auth_cookie_secure: bool | None = None
    auth_login_attempts_per_minute: int = Field(default=10, ge=1, le=1_000)
    auth_requests_per_minute: int = Field(default=60, ge=1, le=10_000)
    workspace_invitation_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    # --- Database roles (ADR-0012, OD-13) ---
    # The API connects as a restricted role that row-level security applies to. POSTGRES_USER
    # is the administrator and schema owner: migrations, seeding and role provisioning only.
    api_db_user: str = Field(default="agentflow_api", min_length=1, max_length=63)
    api_db_password: SecretStr | None = None
    # --- Model gateway (ADR-0013) ---
    # Only the deterministic mock exists until LiteLLM-backed providers arrive (Phase 3).
    model_provider: Literal["mock"] = "mock"
    model_name: str = Field(default="mock-1", min_length=1, max_length=200)
    # --- Background execution (ADR-0010) ---
    # Celery broker: the same Redis server, separate logical database from rate limiting.
    celery_broker_redis_db: int = Field(default=1, ge=0, le=15)
    # Workers connect as a restricted, non-superuser role (see app/db/roles.py).
    worker_db_user: str = Field(default="agentflow_worker", min_length=1, max_length=63)
    worker_db_password: SecretStr | None = None
    outbox_lease_seconds: int = Field(default=60, ge=5, le=3600)
    outbox_max_attempts: int = Field(default=10, ge=1, le=100)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)
    cleanup_max_batches: int = Field(default=20, ge=1, le=1000)

    # Browser origins allowed to make credentialed requests (CORS) and unsafe requests.
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"], min_length=1
    )

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        if self.auth_jwt_active_key_id not in self.auth_jwt_signing_keys:
            raise ValueError("AUTH_JWT_ACTIVE_KEY_ID must name a key in AUTH_JWT_SIGNING_KEYS")
        short = [
            kid
            for kid, key in self.auth_jwt_signing_keys.items()
            if len(key.get_secret_value()) < _MIN_JWT_KEY_LENGTH
        ]
        if short:
            raise ValueError(
                f"AUTH_JWT_SIGNING_KEYS entries must be at least {_MIN_JWT_KEY_LENGTH} "
                f"characters (too short: {', '.join(short)})"
            )
        for origin in self.cors_allowed_origins:
            if not _ORIGIN.fullmatch(origin):
                raise ValueError(f"CORS_ALLOWED_ORIGINS entry is not a plain origin: {origin!r}")
        if self.auth_refresh_token_ttl_seconds > self.auth_session_max_age_seconds:
            raise ValueError("AUTH_REFRESH_TOKEN_TTL_SECONDS cannot exceed the session max age")
        if self.is_production:
            if self.auth_cookie_secure is False:
                raise ValueError("AUTH_COOKIE_SECURE cannot be false in production")
            insecure = [o for o in self.cors_allowed_origins if not o.startswith("https://")]
            if insecure:
                raise ValueError(f"production CORS origins must use https: {', '.join(insecure)}")
        return self

    @model_validator(mode="after")
    def _reject_placeholder_secrets_in_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        secrets: list[tuple[str, SecretStr]] = [
            ("POSTGRES_PASSWORD", self.postgres_password),
            ("REDIS_PASSWORD", self.redis_password),
        ]
        if self.worker_db_password is not None:
            secrets.append(("WORKER_DB_PASSWORD", self.worker_db_password))
        if self.api_db_password is not None:
            secrets.append(("API_DB_PASSWORD", self.api_db_password))
        weak = [
            name
            for name, secret in secrets
            if secret.get_secret_value().strip().lower() in _PLACEHOLDER_SECRETS
        ]
        if any(
            key.get_secret_value().lower().startswith("change-me")
            for key in self.auth_jwt_signing_keys.values()
        ):
            weak.append("AUTH_JWT_SIGNING_KEYS")
        if weak:
            raise ValueError(f"{', '.join(weak)} must be set to a real secret in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def api_docs_enabled(self) -> bool:
        return not self.is_production

    @property
    def cookies_secure(self) -> bool:
        return self.is_production if self.auth_cookie_secure is None else self.auth_cookie_secure

    @property
    def database_url(self) -> URL:
        """The administrator and schema owner (POSTGRES_USER): migrations, seeding, provisioning.

        psycopg 3; create_async_engine selects its async dialect. str(URL) masks the password.
        """
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def api_database_url(self) -> URL:
        """The restricted API role; row-level security applies to it (ADR-0012)."""
        if self.api_db_password is None:
            raise ValueError("API_DB_PASSWORD must be set to run the API")
        return URL.create(
            "postgresql+psycopg",
            username=self.api_db_user,
            password=self.api_db_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def worker_database_url(self) -> URL:
        """psycopg 3 sync URL for Celery workers, using the restricted worker role."""
        if self.worker_db_password is None:
            raise ValueError("WORKER_DB_PASSWORD must be set to run workers")
        return URL.create(
            "postgresql+psycopg",
            username=self.worker_db_user,
            password=self.worker_db_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def celery_broker_url(self) -> str:
        password = quote(self.redis_password.get_secret_value(), safe="")
        return (
            f"redis://:{password}@{self.redis_host}:{self.redis_port}/{self.celery_broker_redis_db}"
        )

    def secret_values(self) -> list[str]:
        """Secret values to redact from logs."""
        values = [
            self.postgres_password.get_secret_value(),
            self.redis_password.get_secret_value(),
            *(key.get_secret_value() for key in self.auth_jwt_signing_keys.values()),
            *([self.worker_db_password.get_secret_value()] if self.worker_db_password else []),
            *([self.api_db_password.get_secret_value()] if self.api_db_password else []),
        ]
        return [v for v in values if len(v) >= _MIN_REDACTABLE_SECRET_LENGTH]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment


def get_app_settings(connection: HTTPConnection) -> Settings:
    settings: Settings = connection.app.state.settings
    return settings
