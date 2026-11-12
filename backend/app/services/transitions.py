"""Async adapter for the API over the synchronous transition engine (app/workflows/engine.py).

The engine is the only code that changes workflow or task status. API services call these
wrappers with their AsyncSession; each runs the engine via `AsyncSession.run_sync`, on the same
connection, transaction and identity map, so loaded objects and the caller's transaction are
shared. Celery workers call the engine directly with a sync Session.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskStatus, Workflow, WorkflowEventType, WorkflowStatus, WorkflowTask
from app.workflows import engine
from app.workflows.engine import (
    InvalidTransitionError,
    TransitionPreconditionError,
    record_event,
)
from app.workflows.state_machine import Actor

__all__ = [
    "InvalidTransitionError",
    "TransitionPreconditionError",
    "reconcile_workflow",
    "record_event",
    "request_analysis",
    "transition_task",
    "transition_workflow",
]


async def transition_workflow(
    session: AsyncSession,
    workflow: Workflow,
    target: WorkflowStatus,
    *,
    actor: Actor,
    reason: str | None = None,
) -> None:
    await session.run_sync(
        lambda sync: engine.transition_workflow(sync, workflow, target, actor=actor, reason=reason)
    )


async def request_analysis(session: AsyncSession, workflow: Workflow, *, actor: Actor) -> None:
    await session.run_sync(lambda sync: engine.request_analysis(sync, workflow, actor=actor))


async def transition_task(
    session: AsyncSession,
    workflow: Workflow,
    task: WorkflowTask,
    target: TaskStatus,
    *,
    actor: Actor,
    reason: str | None = None,
    event_type: WorkflowEventType | None = None,
    extra: dict[str, Any] | None = None,
    reconcile: bool = True,
) -> None:
    await session.run_sync(
        lambda sync: engine.transition_task(
            sync,
            workflow,
            task,
            target,
            actor=actor,
            reason=reason,
            event_type=event_type,
            extra=extra,
            reconcile=reconcile,
        )
    )


async def reconcile_workflow(session: AsyncSession, workflow: Workflow) -> None:
    await session.run_sync(lambda sync: engine.reconcile_workflow(sync, workflow))
