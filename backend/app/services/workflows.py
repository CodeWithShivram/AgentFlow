"""Workflow creation, reads and user-initiated transitions (ADR-0009).

Roles: VIEWER reads; MEMBER creates workflows and drives them; approving a plan
(AWAITING_APPROVAL -> READY) and unblocking (BLOCKED -> RUNNING) need ADMIN, because the
handoff separates approval permission from execution permission.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    AnalysisStatus,
    PlanStatus,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowPlan,
    WorkflowStatus,
    WorkspaceRole,
)
from app.repositories import workflows as repository
from app.schemas.workflows import WorkflowCreateRequest, WorkflowResponse
from app.services.authorization import InsufficientRoleError, WorkspaceAccess, role_at_least
from app.services.idempotency import IdempotencyService, NewRequest, StoredResponse
from app.services.transitions import (
    record_event,
    request_analysis,
    request_plan,
    transition_workflow,
)
from app.workflows.state_machine import Actor

logger = logging.getLogger("agentflow.workflows")

CREATE_OPERATION = "workflows.create"
W = WorkflowStatus
_ADMIN_TRANSITIONS = frozenset({(W.AWAITING_APPROVAL, W.READY), (W.BLOCKED, W.RUNNING)})


class WorkflowNotFoundError(AppError):
    status_code = 404
    code = "WORKFLOW_NOT_FOUND"
    message = "Workflow not found."


class WorkflowRevisionConflictError(AppError):
    status_code = 409
    code = "WORKFLOW_REVISION_CONFLICT"
    message = "The workflow was changed by someone else. Reload it and try again."


class AnalysisNotRetryableError(AppError):
    status_code = 409
    code = "ANALYSIS_NOT_RETRYABLE"
    message = "The analysis can be requested again only after it failed, while planning."


# Analysis may be (re)requested only while the plan can still change.
_ANALYSIS_RETRY_STATUSES = frozenset({W.PLANNING, W.AWAITING_APPROVAL, W.READY})
_ANALYSIS_RETRYABLE = frozenset({AnalysisStatus.FAILED, AnalysisStatus.NOT_REQUESTED})


class PlanNotRegeneratableError(AppError):
    status_code = 409
    code = "PLAN_NOT_REGENERATABLE"
    message = "A new plan draft needs a ready analysis, while planning, and no draft in progress."


@dataclass(frozen=True)
class CreatedWorkflow:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def workflow_response(workflow: Workflow) -> WorkflowResponse:
    return WorkflowResponse.model_validate(workflow, from_attributes=True)


async def lock_for_change(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID, revision: int | None
) -> Workflow:
    """Locks the workflow row; with `revision`, also rejects stale client state."""
    workflow = await repository.lock_workflow(
        session, workspace_id=access.workspace_id, workflow_id=workflow_id
    )
    if workflow is None:
        raise WorkflowNotFoundError()
    if revision is not None and workflow.revision != revision:
        raise WorkflowRevisionConflictError(details={"current_revision": workflow.revision})
    return workflow


async def create_workflow(
    session: AsyncSession,
    access: WorkspaceAccess,
    request: WorkflowCreateRequest,
    *,
    idempotency: IdempotencyService,
    idempotency_key: str | None,
) -> CreatedWorkflow:
    """Creates a PENDING workflow. With an idempotency key, retries replay the first response
    and concurrent duplicates execute once (ADR-0006)."""
    claim: NewRequest | None = None
    if idempotency_key is not None:
        # The user is part of the fingerprint: another member reusing the key gets a conflict.
        payload = {"user_id": str(access.user_id), "request": request.model_dump(mode="json")}
        outcome = await idempotency.begin(
            session,
            workspace_id=access.workspace_id,
            operation=CREATE_OPERATION,
            key=idempotency_key,
            payload=payload,
        )
        if isinstance(outcome, StoredResponse):
            await session.rollback()
            return CreatedWorkflow(outcome.status_code, outcome.body, replayed=True)
        claim = outcome

    settings = request.settings
    workflow = Workflow(
        id=uuid.uuid4(),
        workspace_id=access.workspace_id,
        created_by_user_id=access.user_id,
        prompt=request.prompt,
        status=W.PENDING,
        autonomy_policy=settings.autonomy_policy,
        budget_limit=settings.budget_limit,
        currency=settings.currency,
        max_parallel_tasks=settings.max_parallel_tasks,
        allow_dynamic_agent_proposals=settings.allow_dynamic_agent_proposals,
        deadline=settings.deadline,
        revision=1,
    )
    session.add(workflow)
    await session.flush()
    # The prompt is deliberately not copied into the event (handoff 5.8).
    record_event(
        session,
        workflow,
        WorkflowEventType.WORKFLOW_CREATED,
        payload={"actor": Actor.USER.value, "user_id": str(access.user_id)},
    )
    await session.flush()
    body = workflow_response(workflow).model_dump(mode="json")
    if claim is not None:
        await idempotency.complete(session, claim, status_code=201, body=body)
    await session.commit()
    logger.info("workflow created", extra={"workflow_id": str(workflow.id)})
    return CreatedWorkflow(201, body, replayed=False)


async def list_workflows(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    status: WorkflowStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Workflow], int]:
    return await repository.list_workflows(
        session, workspace_id=access.workspace_id, status=status, limit=limit, offset=offset
    )


async def get_workflow(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> Workflow:
    workflow = await repository.get_workflow(
        session, workspace_id=access.workspace_id, workflow_id=workflow_id
    )
    if workflow is None:
        raise WorkflowNotFoundError()
    return workflow


async def user_transition(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    *,
    target: WorkflowStatus,
    revision: int,
    reason: str | None,
) -> Workflow:
    workflow = await lock_for_change(session, access, workflow_id, revision)
    if (workflow.status, target) in _ADMIN_TRANSITIONS and not role_at_least(
        access.role, WorkspaceRole.ADMIN
    ):
        raise InsufficientRoleError()
    await transition_workflow(session, workflow, target, actor=Actor.USER, reason=reason)
    await session.commit()
    return workflow


async def list_events(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    *,
    after_sequence: int,
    limit: int,
) -> list[WorkflowEvent]:
    workflow = await get_workflow(session, access, workflow_id)
    return await repository.list_events(
        session, workflow, after_sequence=after_sequence, limit=limit
    )


async def retry_analysis(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> Workflow:
    """Requests a new prompt analysis after a failure (or for a workflow planned before
    analysis existed). Does not change the workflow's revision: the plan is untouched."""
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    if (
        workflow.status not in _ANALYSIS_RETRY_STATUSES
        or workflow.analysis_status not in _ANALYSIS_RETRYABLE
    ):
        raise AnalysisNotRetryableError(
            details={
                "status": workflow.status.value,
                "analysis_status": workflow.analysis_status.value,
            }
        )
    await request_analysis(session, workflow, actor=Actor.USER)
    await session.commit()
    return workflow


async def list_plan_drafts(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[WorkflowPlan]:
    workflow = await get_workflow(session, access, workflow_id)
    return await repository.list_plan_drafts(session, workflow)


async def regenerate_plan(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> Workflow:
    """Requests another plan draft (Step 2.3). Earlier drafts are kept; the plan and the
    workflow's revision are untouched until a draft is confirmed (Step 2.5)."""
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    if (
        workflow.status not in _ANALYSIS_RETRY_STATUSES
        or workflow.analysis_status != AnalysisStatus.READY
        or workflow.plan_status == PlanStatus.PENDING
    ):
        raise PlanNotRegeneratableError(
            details={"status": workflow.status.value, "plan_status": workflow.plan_status.value}
        )
    await request_plan(session, workflow, actor=Actor.USER)
    await session.commit()
    return workflow
