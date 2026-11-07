import asyncio
import json
import socket
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings

# Async psycopg needs a selector event loop on Windows (ADR-0005).
LOOP_FACTORY = asyncio.SelectorEventLoop

UNIT_PG_PASSWORD = "unit-test-pg-secret"
UNIT_REDIS_PASSWORD = "unit-test-redis-secret"


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    return ("asyncio", {"loop_factory": LOOP_FACTORY})


def closed_port() -> int:
    """A localhost port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def make_settings(**overrides: Any) -> Settings:
    """Isolated settings: ignores .env and points at ports where nothing listens."""
    values: dict[str, Any] = {
        "app_env": "test",
        "postgres_user": "agentflow",
        "postgres_password": UNIT_PG_PASSWORD,
        "postgres_db": "agentflow",
        "postgres_port": closed_port(),
        "redis_password": UNIT_REDIS_PASSWORD,
        "redis_port": closed_port(),
        "health_check_timeout_seconds": 1.0,
        "redis_socket_timeout_seconds": 1.0,
        "db_connect_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_client(app: FastAPI, *, raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(
        app,
        raise_server_exceptions=raise_server_exceptions,
        backend_options={"loop_factory": LOOP_FACTORY},
    )


def json_log_lines(output: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for line in output.splitlines():
        if line.startswith("{"):
            lines.append(json.loads(line))
    return lines
