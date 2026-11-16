"""Requests that need a person (ADR-0035): clarifications, approvals and escalations.

The platform opens them (app/workflows/engine.py) in the transaction of the change that causes
them. People act here: MEMBERs answer clarifications, ADMINs approve or reject (the handoff
separates approval from execution permission). Escalations are resolved by acting on the task
(reassign, cancel, replan). Every response is attributed and closes the request under the
workflow lock, with an event, in the same transaction as its effect.
"""

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    HumanIntervention,
    InterventionStatus,
    InterventionSubject,
    InterventionType,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.repositories import workflows as workflow_repository
from app.schemas.interventions import InterventionResponse
from app.services.authorization import WorkspaceAccess
from app.services.transitions import transition_task, transition_workflow
from app.services.workflows import lock_for_change
from app.workflows import engine
from app.workflows.state_machine import MAX_ATTEMPTS_PER_TASK, Actor

PROMPT_PREVIEW = 200


class InterventionNotFoundError(AppError):
    status_code = 404
    code = "INTERVENTION_NOT_FOUND"
    message = "Request not found."


class InterventionClosedError(AppError):
    status_code = 409
    code = "INTERVENTION_CLOSED"
    message = "This request was already answered or withdrawn. Reload it."


class InterventionTypeMismatchError(AppError):
    status_code = 409
    code = "INTERVENTION_TYPE_MISMATCH"
    message = "This request does not take that kind of response."


def response(row: HumanIntervention, prompt: str) -> InterventionResponse:
    return InterventionResponse(
        id=row.id,
        workflow_id=row.workflow_id,
        task_id=row.task_id,
        type=row.type,
        subject=row.subject,
        status=row.status,
        requested_action=row.requested_action,
        context=row.context,
        requested_by=row.requested_by,
        requested_by_agent_id=row.requested_by_agent_id,
        resolved_by_user_id=row.resolved_by_user_id,
        resolution=row.resolution,
        resolved_at=row.resolved_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
        workflow_prompt=prompt[:PROMPT_PREVIEW],
    )


async def list_interventions(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    status: InterventionStatus | None,
    workflow_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[InterventionResponse], int]:
    """The workspace's requests, newest first; optionally one status or one workflow."""
    condition = HumanIntervention.workspace_id == access.workspace_id
    if status is not None:
        condition &= HumanIntervention.status == status
    if workflow_id is not None:
        condition &= HumanIntervention.workflow_id == workflow_id
    rows = await session.execute(
        select(HumanIntervention, Workflow.prompt)
        .join(
            Workflow,
            (Workflow.id == HumanIntervention.workflow_id)
            & (Workflow.workspace_id == HumanIntervention.workspace_id),
        )
        .where(condition)
        .order_by(HumanIntervention.created_at.desc(), HumanIntervention.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(
        select(func.count()).select_from(HumanIntervention).where(condition)
    )
    return [response(row, prompt) for row, prompt in rows.all()], total.scalar_one()


async def get_intervention(
    session: AsyncSession, access: WorkspaceAccess, intervention_id: uuid.UUID
) -> InterventionResponse:
    found = (
        await session.execute(
            select(HumanIntervention, Workflow.prompt)
            .join(
                Workflow,
                (Workflow.id == HumanIntervention.workflow_id)
                & (Workflow.workspace_id == HumanIntervention.workspace_id),
            )
            .where(
                HumanIntervention.id == intervention_id,
                HumanIntervention.workspace_id == access.workspace_id,
            )
        )
    ).first()
    if found is None:
        raise InterventionNotFoundError()
    row, prompt = found
    return response(row, prompt)


async def _lock_open(
    session: AsyncSession,
    access: WorkspaceAccess,
    intervention_id: uuid.UUID,
    expected: InterventionType,
) -> tuple[Workflow, HumanIntervention]:
    """Locks the request's workflow, then the request; it must still be OPEN."""
    workflow_id = (
        await session.execute(
            select(HumanIntervention.workflow_id).where(
                HumanIntervention.id == intervention_id,
                HumanIntervention.workspace_id == access.workspace_id,
            )
        )
    ).scalar_one_or_none()
    if workflow_id is None:
        raise InterventionNotFoundError()
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    row = (
        await session.execute(
            select(HumanIntervention)
            .where(
                HumanIntervention.id == intervention_id,
                HumanIntervention.workspace_id == access.workspace_id,
            )
            .with_for_update()
        )
    ).scalar_one()
    if row.status != InterventionStatus.OPEN:
        raise InterventionClosedError()
    if row.type != expected:
        raise InterventionTypeMismatchError()
    return workflow, row


async def _resolve(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow: Workflow,
    row: HumanIntervention,
    resolution: dict[str, str],
) -> None:
    await session.run_sync(
        lambda sync: engine.resolve_intervention(
            sync,
            workflow,
            row,
            status=InterventionStatus.RESOLVED,
            resolution=resolution,
            resolved_by_user_id=access.user_id,
        )
    )


async def answer(
    session: AsyncSession, access: WorkspaceAccess, intervention_id: uuid.UUID, text: str
) -> InterventionResponse:
    """Answers a clarification (MEMBER). The answer reaches the next plan or replan draft; it
    changes no status (the owner's choice for 6.1: nothing pauses for a question)."""
    workflow, row = await _lock_open(
        session, access, intervention_id, InterventionType.CLARIFICATION
    )
    await _resolve(session, access, workflow, row, {"answer": text})
    await session.commit()
    return response(row, workflow.prompt)


async def decide(
    session: AsyncSession,
    access: WorkspaceAccess,
    intervention_id: uuid.UUID,
    decision: Literal["approve", "reject"],
) -> InterventionResponse:
    """Approves or rejects (ADMIN, checked by the route). A plan approval moves the workflow
    to READY or back to PLANNING. An agent's request starts a new attempt by the same agent
    either way, told the decision; the attempt that asked counts, so its limit still applies."""
    workflow, row = await _lock_open(session, access, intervention_id, InterventionType.APPROVAL)
    if row.subject == InterventionSubject.PLAN_APPROVAL:
        if workflow.status != WorkflowStatus.AWAITING_APPROVAL:  # closed by the engine otherwise
            raise InterventionClosedError()
        await _resolve(session, access, workflow, row, {"decision": decision})
        target = WorkflowStatus.READY if decision == "approve" else WorkflowStatus.PLANNING
        await transition_workflow(
            session,
            workflow,
            target,
            actor=Actor.USER,
            reason=f"plan approval: {decision}",
            user_id=access.user_id,
        )
    elif row.subject == InterventionSubject.AGENT_ACTION and row.task_id is not None:
        task = (
            await session.execute(
                select(WorkflowTask).where(
                    WorkflowTask.id == row.task_id,
                    WorkflowTask.workflow_id == workflow.id,
                    WorkflowTask.workspace_id == workflow.workspace_id,
                )
            )
        ).scalar_one()
        if task.status != TaskStatus.WAITING_APPROVAL:
            raise InterventionClosedError()
        await _resolve(session, access, workflow, row, {"decision": decision})
        task.attempt_count = max(
            task.attempt_count,
            await workflow_repository.max_attempt_number(session, workflow, task.id),
        )
        if task.attempt_count >= MAX_ATTEMPTS_PER_TASK:
            await transition_task(
                session,
                workflow,
                task,
                TaskStatus.ESCALATED,
                actor=Actor.SYSTEM,
                reason="ATTEMPTS_EXHAUSTED",
                extra={"attempt_number": task.attempt_count, "decision": decision},
            )
        else:
            await transition_task(
                session,
                workflow,
                task,
                TaskStatus.ASSIGNED,
                actor=Actor.USER,
                reason=f"approval request: {decision}",
                extra={
                    "user_id": str(access.user_id),
                    "decision": decision,
                    "attempt_count": task.attempt_count,
                    "agent_id": str(task.assigned_agent_id),
                    "agent_version_id": str(task.assigned_agent_version_id),
                },
            )
    else:
        raise InterventionTypeMismatchError()
    await session.commit()
    return response(row, workflow.prompt)
