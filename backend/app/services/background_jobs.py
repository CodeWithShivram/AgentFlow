"""Failed background jobs (ADR-0027, OD-18): outbox events the relay gave up on.

They are kept, never deleted (ADR-0010). Members and viewers can see that a workflow's jobs
failed; admins can requeue one. Requeuing is safe: every consumer deduplicates on the event id,
and stale events (a task that moved on, a superseded analysis) change nothing when delivered.
Clients see the error *type* only, never the stored error text, which can name internal hosts.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import OutboxEvent, OutboxStatus, WorkflowEventType
from app.schemas.workflows import FailedJobResponse
from app.services.authorization import WorkspaceAccess
from app.services.transitions import record_event
from app.services.workflows import get_workflow, lock_for_change
from app.workflows.state_machine import Actor


class FailedJobNotFoundError(AppError):
    status_code = 404
    code = "FAILED_JOB_NOT_FOUND"
    message = "No failed background job with this id exists in this workflow."


def error_type(last_error: str | None) -> str | None:
    """The exception class recorded by the relay (`Type: message`), without its message."""
    if not last_error:
        return None
    return last_error.split(":", 1)[0].strip()[:100] or None


def failed_job_response(event: OutboxEvent) -> FailedJobResponse:
    return FailedJobResponse(
        id=event.id,
        event_type=event.event_type,
        workflow_id=event.workflow_id,
        task_id=event.task_id,
        attempts=event.attempts,
        error_type=error_type(event.last_error),
        created_at=event.created_at,
        failed_at=event.updated_at,
    )


async def list_for_workflow(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[OutboxEvent]:
    workflow = await get_workflow(session, access, workflow_id)
    rows = await session.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.workspace_id == workflow.workspace_id,
            OutboxEvent.workflow_id == workflow.id,
            OutboxEvent.status == OutboxStatus.FAILED,
        )
        .order_by(OutboxEvent.updated_at.desc(), OutboxEvent.id)
        .limit(100)
    )
    return list(rows.scalars())


async def list_for_workspace(
    session: AsyncSession, access: WorkspaceAccess, *, limit: int, offset: int
) -> tuple[list[OutboxEvent], int]:
    condition = (OutboxEvent.workspace_id == access.workspace_id) & (
        OutboxEvent.status == OutboxStatus.FAILED
    )
    rows = await session.execute(
        select(OutboxEvent)
        .where(condition)
        .order_by(OutboxEvent.updated_at.desc(), OutboxEvent.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(OutboxEvent).where(condition))
    return list(rows.scalars()), total.scalar_one()


async def retry(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID, event_id: uuid.UUID
) -> OutboxEvent:
    """Requeues a FAILED event of the workflow with fresh attempts, under the workflow lock,
    with a BACKGROUND_JOB_RETRIED event in the same transaction."""
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    event = (
        await session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.workspace_id == workflow.workspace_id,
                OutboxEvent.workflow_id == workflow.id,
                OutboxEvent.status == OutboxStatus.FAILED,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if event is None:
        raise FailedJobNotFoundError()
    previous_attempts = event.attempts
    event.status = OutboxStatus.PENDING
    event.attempts = 0
    event.available_at = func.now()
    event.lease_owner = None
    event.lease_expires_at = None
    record_event(
        session,
        workflow,
        WorkflowEventType.BACKGROUND_JOB_RETRIED,
        task_id=event.task_id,
        payload={
            "actor": Actor.USER.value,
            "user_id": str(access.user_id),
            "event_id": str(event.id),
            "event_type": event.event_type,
            "previous_attempts": previous_attempts,
        },
    )
    await session.commit()
    return event
