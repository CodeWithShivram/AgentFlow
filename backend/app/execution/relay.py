"""Outbox relay: claim, publish (no transaction open), then record the outcome (ADR-0010)."""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox

logger = logging.getLogger("agentflow.outbox")
_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^@/\s]+@", re.IGNORECASE)

Publisher = Callable[[outbox.ClaimedEvent], None]


@dataclass
class RelayReport:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    lease_lost: int = 0


def _safe_error(exc: BaseException) -> str:
    """Error text for `last_error`: the type plus a message with URL credentials removed."""
    return _URL_USERINFO.sub(r"\1[REDACTED]@", f"{type(exc).__name__}: {exc}")[:1000]


def relay_once(
    factory: sessionmaker[Session],
    publish: Publisher,
    *,
    owner: str | None = None,
    batch_size: int,
    lease_seconds: int,
    max_attempts: int,
) -> RelayReport:
    owner = owner or f"relay-{uuid.uuid4().hex}"
    report = RelayReport()
    with session_scope(factory) as session:
        claimed = outbox.claim_batch(
            session, owner=owner, batch_size=batch_size, lease_seconds=lease_seconds
        )
    report.claimed = len(claimed)
    for event in claimed:
        try:
            publish(event)  # no database transaction is open here
        except Exception as exc:  # broker unavailable, unknown route, ...
            report.failed += 1
            with session_scope(factory) as session:
                kept = outbox.mark_failed(
                    session,
                    event.id,
                    owner=owner,
                    attempts=event.attempts,
                    max_attempts=max_attempts,
                    error=_safe_error(exc),
                )
            if not kept:
                report.lease_lost += 1
            logger.warning(
                "outbox publish failed",
                extra={"event_id": str(event.id), "error_type": type(exc).__name__},
            )
            continue
        with session_scope(factory) as session:
            if outbox.mark_published(session, event.id, owner=owner):
                report.published += 1
            else:
                # Our lease expired and another relay reclaimed the row; it will publish again
                # (at-least-once) and consumers deduplicate on the event ID.
                report.lease_lost += 1
    return report
