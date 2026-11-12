from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import REPO_ROOT_ENV_FILE, Settings
from tests.conftest import (
    UNIT_API_DB_PASSWORD,
    UNIT_JWT_KEY,
    UNIT_PG_PASSWORD,
    UNIT_REDIS_PASSWORD,
    make_settings,
)

_REQUIRED_ENV = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_PASSWORD",
    "AUTH_JWT_SIGNING_KEYS",
    "AUTH_JWT_ACTIVE_KEY_ID",
)
PRODUCTION = {"app_env": "production", "cors_allowed_origins": ["https://app.example.com"]}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_REQUIRED_ENV, "APP_ENV", "POSTGRES_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_env_file_is_repo_root() -> None:
    assert (REPO_ROOT_ENV_FILE.parent / ".env.example").is_file()
    assert (REPO_ROOT_ENV_FILE.parent / "backend").is_dir()


def test_loads_values_from_env_file(tmp_path: Path, clean_env: None) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=test\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=file-pg-secret\n"
        "POSTGRES_DB=d\nPOSTGRES_PORT=6543\nREDIS_PASSWORD=file-redis-secret\n"
        'AUTH_JWT_SIGNING_KEYS={"k":"file-jwt-key-0123456789abcdef0123456789"}\n'
        "AUTH_JWT_ACTIVE_KEY_ID=k\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "test"
    assert settings.postgres_port == 6543
    assert settings.postgres_password.get_secret_value() == "file-pg-secret"
    assert settings.auth_jwt_signing_keys["k"].get_secret_value().startswith("file-jwt-key")


def test_missing_required_settings_are_rejected(clean_env: None) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    missing = {err["loc"][0] for err in exc_info.value.errors() if err["type"] == "missing"}
    assert missing == {
        "postgres_user",
        "postgres_password",
        "postgres_db",
        "redis_password",
        "auth_jwt_signing_keys",
        "auth_jwt_active_key_id",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"app_env": "staging"},
        {"log_level": "TRACE"},
        {"postgres_port": 0},
        {"redis_port": 70000},
        {"postgres_password": ""},
        {"health_check_timeout_seconds": 0},
        {"db_pool_size": 0},
        {"auth_access_token_ttl_seconds": 3600},
        {"auth_jwt_active_key_id": "missing"},
        {"auth_jwt_signing_keys": {"test-k1": "too-short"}},
        {"cors_allowed_origins": ["*"]},
        {"cors_allowed_origins": ["http://localhost:3000/path"]},
        {"cors_allowed_origins": []},
        {"auth_refresh_token_ttl_seconds": 7200, "auth_session_max_age_seconds": 3600},
    ],
)
def test_invalid_values_are_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**override)


def test_production_rejects_placeholder_secrets() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(**PRODUCTION, postgres_password="change-me")

    assert "POSTGRES_PASSWORD must be set to a real secret in production" in str(exc_info.value)


def test_production_rejects_placeholder_jwt_key() -> None:
    key = "change-me-generate-a-random-secret-of-at-least-32-chars"
    with pytest.raises(ValidationError) as exc_info:
        make_settings(**PRODUCTION, auth_jwt_signing_keys={"test-k1": key})

    assert "AUTH_JWT_SIGNING_KEYS" in str(exc_info.value)
    assert key not in str(exc_info.value)


@pytest.mark.parametrize(
    "override",
    [{"cors_allowed_origins": ["http://app.example.com"]}, {"auth_cookie_secure": False}],
)
def test_production_requires_https_origins_and_secure_cookies(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{**PRODUCTION, **override})


def test_production_with_real_secrets_is_hardened() -> None:
    settings = make_settings(**PRODUCTION)

    assert settings.is_production
    assert not settings.api_docs_enabled
    assert settings.cookies_secure


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_enables_api_docs_and_plain_cookies(env: str) -> None:
    settings = make_settings(app_env=env)

    assert settings.api_docs_enabled
    assert not settings.cookies_secure
    assert make_settings(app_env=env, auth_cookie_secure=True).cookies_secure


def test_secrets_are_masked_in_repr_dump_and_database_url() -> None:
    settings = make_settings()

    rendered = [
        repr(settings),
        str(settings),
        settings.model_dump_json(),
        str(settings.database_url),
    ]

    for text in rendered:
        assert UNIT_PG_PASSWORD not in text
        assert UNIT_REDIS_PASSWORD not in text
        assert UNIT_JWT_KEY not in text
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.password == UNIT_PG_PASSWORD


def test_secret_values_include_jwt_keys_and_skip_short_values() -> None:
    settings = make_settings(postgres_password="abc")

    assert settings.secret_values() == [UNIT_REDIS_PASSWORD, UNIT_JWT_KEY, UNIT_API_DB_PASSWORD]


def test_api_database_url_uses_the_restricted_role_and_requires_its_password() -> None:
    settings = make_settings()
    url = settings.api_database_url

    assert url.username == "agentflow_api"
    assert url.password == UNIT_API_DB_PASSWORD
    assert url.database == settings.database_url.database
    with pytest.raises(ValueError, match="API_DB_PASSWORD"):
        _ = make_settings(api_db_password=None).api_database_url
    with pytest.raises(ValueError, match="API_DB_PASSWORD must be set to a real secret"):
        make_settings(**PRODUCTION, api_db_password="change-me")
