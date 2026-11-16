"""Requests that need a person (ADR-0035): clarifications, approvals and escalations.

The platform opens them (app/workflows/engine.py) in the transaction of the change that causes
them. People act here: MEMBERs answer clarifications, ADMINs approve or reject (the handoff
separates approval from execution permission). Escalations are resolved by acting on the task
(reassign, cancel, replan). Every response is attributed and closes the request under the
workflow lock, with an event, in the same transaction as its effect.
"""

import uuid
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    ApprovalDecision,
    HumanIntervention,
    InterventionStatus,
    InterventionSubject,
    InterventionType,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
    WorkspaceRole,
)
from app.schemas.interventions import (
    ApprovalDecisionResponse,
    ApprovalRecord,
    InterventionResponse,
)
from app.services.authorization import InsufficientRoleError, WorkspaceAccess, role_at_least
from app.services.transitions import transition_workflow
from app.services.workflows import lock_for_change
from app.workflows import engine
from app.workflows.state_machine import Actor

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


def approval_record(record: ApprovalDecision, creator_id: uuid.UUID) -> ApprovalRecord:
    return ApprovalRecord(
        decision=record.decision,
        then=record.then,
        note=record.note,
        required_role=record.required_role,
        decided_by_user_id=record.decided_by_user_id,
        decided_role=record.decided_role,
        expired=record.expired,
        by_workflow_creator=record.decided_by_user_id == creator_id,
        action_fingerprint=record.action_fingerprint,
        decided_at=record.decided_at,
    )


def response(
    row: HumanIntervention,
    prompt: str,
    record: ApprovalDecision | None = None,
    creator_id: uuid.UUID | None = None,
) -> InterventionResponse:
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
        required_role=row.required_role,
        decision_record=(
            approval_record(record, creator_id)
            if record is not None and creator_id is not None
            else None
        ),
    )


def _with_records() -> Any:
    """Requests with their workflow's prompt and creator and, once decided, their audit."""
    return (
        select(HumanIntervention, Workflow.prompt, Workflow.created_by_user_id, ApprovalDecision)
        .join(
            Workflow,
            (Workflow.id == HumanIntervention.workflow_id)
            & (Workflow.workspace_id == HumanIntervention.workspace_id),
        )
        .outerjoin(
            ApprovalDecision,
            (ApprovalDecision.intervention_id == HumanIntervention.id)
            & (ApprovalDecision.workspace_id == HumanIntervention.workspace_id),
        )
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
        _with_records()
        .where(condition)
        .order_by(HumanIntervention.created_at.desc(), HumanIntervention.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(
        select(func.count()).select_from(HumanIntervention).where(condition)
    )
    return [
        response(row, prompt, record, creator) for row, prompt, creator, record in rows.all()
    ], total.scalar_one()


async def get_intervention(
    session: AsyncSession, access: WorkspaceAccess, intervention_id: uuid.UUID
) -> InterventionResponse:
    found = (
        await session.execute(
            _with_records().where(
                HumanIntervention.id == intervention_id,
                HumanIntervention.workspace_id == access.workspace_id,
            )
        )
    ).first()
    if found is None:
        raise InterventionNotFoundError()
    row, prompt, creator, record = found
    return response(row, prompt, record, creator)


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
    resolution: dict[str, Any],
) -> None:
    await session.run_sync(
        lambda sync: engine.resolve_intervention(
            sync,
            workflow,
            row,
            status=InterventionStatus.RESOLVED,
            resolution=resolution,
            resolved_by_user_id=access.user_id,
            decided_role=access.role,
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
    return await get_intervention(session, access, intervention_id)


async def decide(
    session: AsyncSession,
    access: WorkspaceAccess,
    intervention_id: uuid.UUID,
    decision: Literal["approve", "reject"],
    *,
    stop: bool = False,
    note: str | None = None,
) -> InterventionResponse:
    """Approves or rejects (ADMIN, checked by the route). A plan approval moves the workflow
    to READY or back to PLANNING. An agent's request starts a new attempt by the same agent,
    told the decision, unless a rejection `stop`s the task for people (ADR-0037); the attempt
    that asked counts, so its limit still applies. A rejection's `note` is for people only."""
    workflow, row = await _lock_open(session, access, intervention_id, InterventionType.APPROVAL)
    # Per-action authorization (ADR-0038): the request names the least role that may decide it.
    if row.required_role is None or not role_at_least(
        access.role, WorkspaceRole(row.required_role)
    ):
        raise InsufficientRoleError()
    if row.subject == InterventionSubject.PLAN_APPROVAL:
        if workflow.status != WorkflowStatus.AWAITING_APPROVAL:  # closed by the engine otherwise
            raise InterventionClosedError()
        if stop:
            raise InterventionTypeMismatchError()
        resolution = {"decision": decision, **({"note": note} if note else {})}
        await _resolve(session, access, workflow, row, resolution)
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
        if stop and decision != "reject":
            raise InterventionTypeMismatchError()
        await session.run_sync(
            lambda sync: engine.decide_agent_action(
                sync,
                workflow,
                row,
                task,
                decision=decision,
                stop=stop,
                note=note,
                user_id=access.user_id,
                role=access.role,
            )
        )
    else:
        raise InterventionTypeMismatchError()
    await session.commit()
    return await get_intervention(session, access, intervention_id)


async def list_approvals(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    workflow_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ApprovalDecisionResponse], int]:
    """The workspace's approval decisions (ADR-0038), newest first."""
    condition = ApprovalDecision.workspace_id == access.workspace_id
    if workflow_id is not None:
        condition &= ApprovalDecision.workflow_id == workflow_id
    rows = await session.execute(
        select(
            ApprovalDecision,
            HumanIntervention.requested_action,
            Workflow.prompt,
            Workflow.created_by_user_id,
        )
        .join(
            HumanIntervention,
            (HumanIntervention.id == ApprovalDecision.intervention_id)
            & (HumanIntervention.workspace_id == ApprovalDecision.workspace_id),
        )
        .join(
            Workflow,
            (Workflow.id == ApprovalDecision.workflow_id)
            & (Workflow.workspace_id == ApprovalDecision.workspace_id),
        )
        .where(condition)
        .order_by(ApprovalDecision.decided_at.desc(), ApprovalDecision.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(
        select(func.count()).select_from(ApprovalDecision).where(condition)
    )
    items = [
        ApprovalDecisionResponse(
            **approval_record(record, creator).model_dump(),
            id=record.id,
            intervention_id=record.intervention_id,
            workflow_id=record.workflow_id,
            task_id=record.task_id,
            execution_id=record.execution_id,
            subject=record.subject,
            requested_action=action,
            workflow_prompt=prompt[:PROMPT_PREVIEW],
        )
        for record, action, prompt, creator in rows.all()
    ]
    return items, total.scalar_one()
