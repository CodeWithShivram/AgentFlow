"""Relay heartbeat and backlog status (Step 4.3, ADR-0027)."""

import pytest

from app.execution import heartbeat
from app.services.background_jobs import error_type

NOW = 1_000_000.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "unavailable"),
        ("not json", "unavailable"),
        ('{"at": "x", "oldest_due_seconds": 0}', "unavailable"),
        ('{"at": 1}', "unavailable"),
        (heartbeat.encode(at=NOW - 61, oldest_due=0), "unavailable"),  # silent relay
        (heartbeat.encode(at=NOW - 5, oldest_due=301), "lagging"),  # backlog too old
        (heartbeat.encode(at=NOW - 5, oldest_due=300), "ok"),
        (heartbeat.encode(at=NOW, oldest_due=0).encode(), "ok"),  # bytes from Redis
    ],
)
def test_worker_status(raw: str | bytes | None, expected: str) -> None:
    assert heartbeat.status(raw, now=NOW) == expected


def test_failed_jobs_expose_only_the_error_type() -> None:
    assert error_type("ConnectionError: Error 10061 connecting to 10.0.0.5:6379.") == (
        "ConnectionError"
    )
    assert error_type("PermanentPublishError: payload lacks task_id") == "PermanentPublishError"
    assert error_type(None) is None and error_type("") is None
