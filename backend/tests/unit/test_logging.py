import json
import logging
import sys
from typing import Any

from app.core.logging import REDACTED, JsonFormatter

SECRET = "registered-secret-value"


def _format(
    message: str, *args: object, extra: dict[str, Any] | None = None, exc: bool = False
) -> dict[str, Any]:
    formatter = JsonFormatter("agentflow-api", "test", secret_values=[SECRET])
    record = logging.LogRecord("agentflow.test", logging.INFO, __file__, 1, message, args, None)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    if exc:
        try:
            raise RuntimeError(f"failed with {SECRET}")
        except RuntimeError:
            record.exc_info = sys.exc_info()
    result: dict[str, Any] = json.loads(formatter.format(record))
    return result


def test_emits_structured_fields() -> None:
    line = _format("hello %s", "world", extra={"status_code": 200})

    assert line["message"] == "hello world"
    assert line["level"] == "INFO"
    assert line["logger"] == "agentflow.test"
    assert line["service"] == "agentflow-api"
    assert line["environment"] == "test"
    assert line["timestamp"].endswith("+00:00")
    assert line["status_code"] == 200


def test_drops_uvicorn_color_message() -> None:
    assert "color_message" not in _format("x", extra={"color_message": "\x1b[36mx\x1b[0m"})


def test_includes_explicit_request_id() -> None:
    assert _format("x", extra={"request_id": "req-1"})["request_id"] == "req-1"


def test_redacts_sensitive_keys_recursively() -> None:
    line = _format(
        "login",
        extra={
            "password": "hunter2",
            "payload": {"api_key": "k", "nested": [{"Authorization": "Bearer t"}], "ok": 1},
            "access_token": "t",
        },
    )

    assert line["password"] == REDACTED
    assert line["access_token"] == REDACTED
    assert line["payload"]["api_key"] == REDACTED
    assert line["payload"]["nested"][0]["Authorization"] == REDACTED
    assert line["payload"]["ok"] == 1


def test_redacts_url_credentials_and_registered_secrets() -> None:
    line = _format("connect postgresql+psycopg://agentflow:pw@db:5432/x using %s", SECRET)

    assert "agentflow:pw" not in line["message"]
    assert SECRET not in line["message"]
    assert line["message"] == (
        f"connect postgresql+psycopg://{REDACTED}@db:5432/x using {REDACTED}"
    )


def test_exception_traceback_is_included_and_redacted() -> None:
    line = _format("boom", exc=True)

    assert line["exception"]["type"] == "RuntimeError"
    assert "Traceback" in line["exception"]["traceback"]
    assert SECRET not in json.dumps(line)
