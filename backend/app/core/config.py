"""Centralized application settings, loaded from the environment and the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# Values that must never be used as real secrets in production.
_PLACEHOLDER_SECRETS = frozenset({"change-me", "changeme", "password", "secret"})
# Shorter secrets are not redacted by value, to avoid mangling ordinary log text.
_MIN_REDACTABLE_SECRET_LENGTH = 6

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

    @model_validator(mode="after")
    def _reject_placeholder_secrets_in_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        weak = [
            name
            for name, secret in (
                ("POSTGRES_PASSWORD", self.postgres_password),
                ("REDIS_PASSWORD", self.redis_password),
            )
            if secret.get_secret_value().strip().lower() in _PLACEHOLDER_SECRETS
        ]
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
    def database_url(self) -> URL:
        # psycopg 3; create_async_engine selects its async dialect. str(URL) masks the password.
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def secret_values(self) -> list[str]:
        """Secret values to redact from logs."""
        values = (self.postgres_password.get_secret_value(), self.redis_password.get_secret_value())
        return [v for v in values if len(v) >= _MIN_REDACTABLE_SECRET_LENGTH]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
