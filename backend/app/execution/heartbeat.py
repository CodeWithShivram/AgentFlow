"""Relay heartbeat and outbox backlog (ADR-0027).

Each relay run (every 5 s, Celery Beat) records when it ran and how long the oldest *due*
pending outbox event has been waiting. Readiness reads that record from Redis: the API cannot
measure the backlog itself, because row-level security shows it no outbox rows without a user.
Redis holds only this short-lived cache entry; PostgreSQL stays the source of truth.
"""

import json
import time
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OutboxEvent, OutboxStatus

KEY = "agentflow:workers:relay_heartbeat"
# The entry expires on its own if workers stop, so a stale heartbeat is never read as healthy.
TTL_SECONDS = 300
# Unhealthy when the relay has been silent this long (it runs every 5 s)...
MAX_SILENCE_SECONDS = 60
# ...or when a due event has waited this long without being published.
MAX_BACKLOG_SECONDS = 300

WorkerStatus = Literal["ok", "unavailable", "lagging"]


def oldest_due_seconds(session: Session) -> int:
    """Seconds the oldest PENDING event has been due (0 when nothing is due)."""
    due_since = session.execute(
        select(func.min(OutboxEvent.available_at)).where(
            OutboxEvent.status == OutboxStatus.PENDING, OutboxEvent.available_at <= func.now()
        )
    ).scalar_one_or_none()
    if due_since is None:
        return 0
    waited = session.execute(select(func.extract("epoch", func.now() - due_since))).scalar_one()
    return max(0, int(waited))


def encode(*, at: float, oldest_due: int) -> str:
    return json.dumps({"at": at, "oldest_due_seconds": oldest_due})


def status(raw: bytes | str | None, *, now: float | None = None) -> WorkerStatus:
    """`unavailable` without a recent heartbeat, `lagging` with a backlog, else `ok`."""
    if raw is None:
        return "unavailable"
    try:
        record = json.loads(raw)
        at, oldest_due = float(record["at"]), int(record["oldest_due_seconds"])
    except (ValueError, KeyError, TypeError):
        return "unavailable"
    if (now if now is not None else time.time()) - at > MAX_SILENCE_SECONDS:
        return "unavailable"
    return "lagging" if oldest_due > MAX_BACKLOG_SECONDS else "ok"
