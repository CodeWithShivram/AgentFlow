"""Scheduled cleanup of expired records (OD-12, ADR-0010).

Safe to retry and to run concurrently: each batch is its own short transaction deleting at most
`batch_size` rows selected by key. If two runs pick the same rows, the second waits for the
first's row locks, then finds them gone and deletes nothing, so each row is deleted exactly
once. (FOR UPDATE SKIP LOCKED is not used: row locks need UPDATE privilege, which the restricted
worker role deliberately lacks on these tables.) A run is bounded by `max_batches` per
category. Nothing is removed before it expires, and records needed for security/audit history
are kept longer or kept:

| Records                                   | Deleted when                                     |
|-------------------------------------------|--------------------------------------------------|
| idempotency_keys                          | expired                                          |
| refresh_tokens                            | expired more than 30 days ago                    |
| auth_sessions                             | expired more than 30 days ago; sessions revoked  |
|                                           | for refresh-token reuse: more than 90 days ago   |
| workspace_invitations (never accepted)    | expired more than 30 days ago                    |
| workspace_invitations (accepted)          | never (they record how membership was granted)   |
| outbox_events (PUBLISHED)                 | published more than 7 days ago (FAILED are kept) |
| inbox_messages                            | processed more than 7 days ago                   |
| checkpoints (ADR-0026)                    | beyond each workflow's 20 newest; all once the   |
|                                           | workflow no longer exists                        |
| checkpoint_writes / checkpoint_blobs      | no remaining checkpoint refers to them           |
"""

import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import Delete, String, and_, cast, delete, exists, func, or_, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.models import (
    AuthSession,
    Checkpoint,
    CheckpointBlob,
    CheckpointWrite,
    IdempotencyKey,
    InboxMessage,
    OutboxEvent,
    OutboxStatus,
    RefreshToken,
    SessionRevocationReason,
    Workflow,
    WorkspaceInvitation,
)

logger = logging.getLogger("agentflow.cleanup")

AUTH_RECORD_RETENTION = timedelta(days=30)
SECURITY_RECORD_RETENTION = timedelta(days=90)
INVITATION_RETENTION = timedelta(days=30)
MESSAGE_RETENTION = timedelta(days=7)
# Orchestration checkpoints kept per workflow (thread), newest first (ADR-0026).
CHECKPOINTS_KEPT_PER_WORKFLOW = 20


def _before(retention: timedelta) -> Any:
    return func.now() - retention


def _idempotency(batch: int) -> Delete:
    ids = select(IdempotencyKey.id).where(IdempotencyKey.expires_at <= func.now())
    return delete(IdempotencyKey).where(IdempotencyKey.id.in_(ids.limit(batch)))


def _refresh_tokens(batch: int) -> Delete:
    ids = select(RefreshToken.id).where(RefreshToken.expires_at < _before(AUTH_RECORD_RETENTION))
    return delete(RefreshToken).where(RefreshToken.id.in_(ids.limit(batch)))


def _sessions(batch: int) -> Delete:
    reuse = SessionRevocationReason.REFRESH_TOKEN_REUSE
    ids = select(AuthSession.id).where(
        or_(
            and_(
                AuthSession.expires_at < _before(AUTH_RECORD_RETENTION),
                AuthSession.revoked_reason.is_distinct_from(reuse),
            ),
            AuthSession.expires_at < _before(SECURITY_RECORD_RETENTION),
        )
    )
    return delete(AuthSession).where(AuthSession.id.in_(ids.limit(batch)))


def _invitations(batch: int) -> Delete:
    ids = select(WorkspaceInvitation.id).where(
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.expires_at < _before(INVITATION_RETENTION),
    )
    return delete(WorkspaceInvitation).where(WorkspaceInvitation.id.in_(ids.limit(batch)))


def _outbox(batch: int) -> Delete:
    ids = select(OutboxEvent.id).where(
        OutboxEvent.status == OutboxStatus.PUBLISHED,
        OutboxEvent.published_at < _before(MESSAGE_RETENTION),
    )
    return delete(OutboxEvent).where(OutboxEvent.id.in_(ids.limit(batch)))


def _inbox(batch: int) -> Delete:
    keys = select(InboxMessage.event_id, InboxMessage.consumer).where(
        InboxMessage.processed_at < _before(MESSAGE_RETENTION)
    )
    return delete(InboxMessage).where(
        tuple_(InboxMessage.event_id, InboxMessage.consumer).in_(keys.limit(batch))
    )


def _checkpoints(batch: int) -> Delete:
    # Checkpoint ids are time-ordered (UUIDv6), so ordering by id puts the newest first.
    ranked = select(
        Checkpoint.thread_id,
        Checkpoint.checkpoint_ns,
        Checkpoint.checkpoint_id,
        func.row_number()
        .over(
            partition_by=(Checkpoint.thread_id, Checkpoint.checkpoint_ns),
            order_by=Checkpoint.checkpoint_id.desc(),
        )
        .label("position"),
    ).subquery()
    workflow_exists = exists().where(cast(Workflow.id, String) == ranked.c.thread_id)
    keys = (
        select(ranked.c.thread_id, ranked.c.checkpoint_ns, ranked.c.checkpoint_id)
        .where(or_(ranked.c.position > CHECKPOINTS_KEPT_PER_WORKFLOW, ~workflow_exists))
        .limit(batch)
    )
    return delete(Checkpoint).where(
        tuple_(Checkpoint.thread_id, Checkpoint.checkpoint_ns, Checkpoint.checkpoint_id).in_(keys)
    )


def _checkpoint_writes(batch: int) -> Delete:
    has_checkpoint = exists().where(
        Checkpoint.thread_id == CheckpointWrite.thread_id,
        Checkpoint.checkpoint_ns == CheckpointWrite.checkpoint_ns,
        Checkpoint.checkpoint_id == CheckpointWrite.checkpoint_id,
    )
    keys = select(
        CheckpointWrite.thread_id,
        CheckpointWrite.checkpoint_ns,
        CheckpointWrite.checkpoint_id,
        CheckpointWrite.task_id,
        CheckpointWrite.idx,
    ).where(~has_checkpoint)
    return delete(CheckpointWrite).where(
        tuple_(
            CheckpointWrite.thread_id,
            CheckpointWrite.checkpoint_ns,
            CheckpointWrite.checkpoint_id,
            CheckpointWrite.task_id,
            CheckpointWrite.idx,
        ).in_(keys.limit(batch))
    )


def _checkpoint_blobs(batch: int) -> Delete:
    # A blob is one channel value at one version; checkpoints refer to it in channel_versions.
    referenced = exists().where(
        Checkpoint.thread_id == CheckpointBlob.thread_id,
        Checkpoint.checkpoint_ns == CheckpointBlob.checkpoint_ns,
        Checkpoint.checkpoint["channel_versions"][CheckpointBlob.channel].astext
        == CheckpointBlob.version,
    )
    keys = select(
        CheckpointBlob.thread_id,
        CheckpointBlob.checkpoint_ns,
        CheckpointBlob.channel,
        CheckpointBlob.version,
    ).where(~referenced)
    return delete(CheckpointBlob).where(
        tuple_(
            CheckpointBlob.thread_id,
            CheckpointBlob.checkpoint_ns,
            CheckpointBlob.channel,
            CheckpointBlob.version,
        ).in_(keys.limit(batch))
    )


CATEGORIES: dict[str, Callable[[int], Delete]] = {
    "idempotency_keys": _idempotency,
    "refresh_tokens": _refresh_tokens,
    "auth_sessions": _sessions,
    "workspace_invitations": _invitations,
    "outbox_events": _outbox,
    "inbox_messages": _inbox,
    # Checkpoints first, then the writes and blobs they no longer refer to.
    "checkpoints": _checkpoints,
    "checkpoint_writes": _checkpoint_writes,
    "checkpoint_blobs": _checkpoint_blobs,
}


def run_cleanup(
    factory: sessionmaker[Session], *, batch_size: int, max_batches: int
) -> dict[str, int]:
    """Deletes expired records category by category. Returns the count deleted per category."""
    started = time.perf_counter()
    counts: dict[str, int] = {}
    for name, build in CATEGORIES.items():
        deleted = 0
        for _ in range(max_batches):
            with session_scope(factory) as session:
                result = session.execute(
                    build(batch_size).execution_options(synchronize_session=False)
                )
                session.commit()
            removed = int(getattr(result, "rowcount", 0) or 0)
            deleted += removed
            if removed < batch_size:
                break
        counts[name] = deleted
    logger.info(
        "cleanup finished",
        extra={
            "deleted": counts,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return counts
