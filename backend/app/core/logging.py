"""Structured JSON logging with request correlation and secret redaction."""

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.request_id import get_request_id

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|credential|dsn|database_url",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^@/\s]+@", re.IGNORECASE)
# Standard LogRecord attributes, plus uvicorn's ANSI-coloured duplicate of the message.
_RESERVED_RECORD_ATTRS = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None)))
    | {"message", "asctime", "color_message"}
)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str, secret_values: Iterable[str] = ()) -> None:
        super().__init__()
        self._service = service
        self._environment = environment
        self._secret_values = sorted(set(secret_values), key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "environment": self._environment,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if (
                key not in _RESERVED_RECORD_ATTRS
                and key != "request_id"
                and not key.startswith("_")
            ):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(self._redact(payload), default=str)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: REDACTED if _SENSITIVE_KEY.search(str(k)) else self._redact(v)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self._redact(v) for v in value]
        if isinstance(value, str):
            text = _URL_USERINFO.sub(rf"\g<scheme>{REDACTED}@", value)
            for secret in self._secret_values:
                text = text.replace(secret, REDACTED)
            return text
        return value


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to the current sys.stdout, so redirection (e.g. test capture) is honoured."""

    _agentflow_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_agentflow_handler", False)]:
        root.removeHandler(handler)
    handler = _StdoutHandler()
    handler.setFormatter(
        JsonFormatter(settings.service_name, settings.app_env, settings.secret_values())
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Route uvicorn's own loggers through the JSON handler. Its access log is replaced by
    # the request middleware log, which carries the request ID.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
