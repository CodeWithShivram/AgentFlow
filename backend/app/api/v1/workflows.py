"""Workflow and task endpoints under /workspaces/{workspace_id} (ADR-0009).

Routes check the minimum role and translate HTTP; rules live in app.services.workflows,
app.services.tasks and app.services.transitions.
"""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, SettingsDep, workspace_role
from app.models import WorkflowStatus, WorkflowTask, WorkspaceRole
from app.repositories import workflows as repository
from app.schemas.workflows import (
    AssignRequest,
    DependencyRequest,
    EventPage,
    ExecutionList,
    ExecutionResponse,
    PlanConfirmRequest,
    PlanDraftList,
    PlanDraftPreview,
    PlanDraftSummary,
    PlanIssueResponse,
    RevisionReasonRequest,
    TaskCreateRequest,
    TaskResponse,
    TaskUpdateRequest,
    WorkflowCreateRequest,
    WorkflowEventResponse,
    WorkflowResponse,
    WorkflowTransitionRequest,
)
from app.schemas.workspaces import Page
from app.services import tasks as task_service
from app.services import workflows as service
from app.services.authorization import WorkspaceAccess
from app.services.idempotency import IdempotencyService

router = APIRouter(prefix="/workspaces/{workspace_id}/workflows", tags=["workflows"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Member = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.MEMBER))]
IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)]


def _task(task: WorkflowTask, depends_on: list[uuid.UUID]) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        workflow_id=task.workflow_id,
        title=task.title,
        description=task.description,
        required_skills=task.required_skills,
        input_references=task.input_references,
        expected_output_schema=task.expected_output_schema,
        acceptance_criteria=task.acceptance_criteria,
        deliverables=task.deliverables,
        priority=task.priority,
        required=task.required,
        removed=task.removed_at is not None,
        status=task.status,
        depends_on=sorted(depends_on),
        assigned_agent_id=task.assigned_agent_id,
        assigned_agent_version_id=task.assigned_agent_version_id,
        attempt_count=task.attempt_count,
        revision=task.revision,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


async def _task_response(
    session: DbSession, access: WorkspaceAccess, workflow_id: uuid.UUID, task: WorkflowTask
) -> TaskResponse:
    workflow = await service.get_workflow(session, access, workflow_id)
    deps = await task_service.dependency_map(session, workflow)
    return _task(task, deps.get(task.id, []))


# --- workflows -------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkflowResponse)
async def create_workflow(
    body: WorkflowCreateRequest,
    access: Member,
    session: DbSession,
    settings: SettingsDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> JSONResponse:
    """Creates a PENDING workflow. Send `Idempotency-Key` to make retries safe."""
    idempotency = IdempotencyService(ttl=timedelta(seconds=settings.idempotency_key_ttl_seconds))
    created = await service.create_workflow(
        session, access, body, idempotency=idempotency, idempotency_key=idempotency_key
    )
    headers = {"Idempotent-Replayed": "true"} if created.replayed else None
    return JSONResponse(created.body, status_code=created.status_code, headers=headers)


@router.get("")
async def list_workflows(
    access: Viewer,
    session: DbSession,
    status_filter: Annotated[WorkflowStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[WorkflowResponse]:
    workflows, total = await service.list_workflows(
        session, access, status=status_filter, limit=limit, offset=offset
    )
    items = [service.workflow_response(w) for w in workflows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> WorkflowResponse:
    return service.workflow_response(await service.get_workflow(session, access, workflow_id))


@router.post("/{workflow_id}/transitions")
async def transition_workflow(
    workflow_id: uuid.UUID, body: WorkflowTransitionRequest, access: Member, session: DbSession
) -> WorkflowResponse:
    """Requests a status change. Only transitions a user may perform are accepted; approving a
    plan and unblocking need ADMIN."""
    workflow = await service.user_transition(
        session,
        access,
        workflow_id,
        target=body.to_status,
        revision=body.revision,
        reason=body.reason,
    )
    return service.workflow_response(workflow)


@router.post("/{workflow_id}/analysis")
async def retry_analysis(
    workflow_id: uuid.UUID, access: Member, session: DbSession
) -> WorkflowResponse:
    """Requests the prompt analysis again after it failed (Step 2.2). It runs in a worker;
    poll the workflow or its events for the result."""
    return service.workflow_response(await service.retry_analysis(session, access, workflow_id))


@router.get("/{workflow_id}/plan-drafts")
async def list_plan_drafts(
    workflow_id: uuid.UUID, access: Viewer, session: DbSession
) -> PlanDraftList:
    """Generated plan drafts, newest first (Step 2.3). Summaries only."""
    drafts = await service.list_plan_drafts(session, access, workflow_id)
    return PlanDraftList(
        items=[PlanDraftSummary.model_validate(d, from_attributes=True) for d in drafts]
    )


@router.get("/{workflow_id}/plan-drafts/{version}")
async def preview_plan_draft(
    workflow_id: uuid.UUID,
    version: Annotated[int, Path(ge=1)],
    access: Viewer,
    session: DbSession,
) -> PlanDraftPreview:
    """One plan draft with its DAG validation: stages and issues (Step 2.4)."""
    preview = await service.preview_plan_draft(session, access, workflow_id, version)
    draft, report = preview.draft, preview.report
    return PlanDraftPreview(
        version=draft.version,
        task_count=draft.task_count,
        prompt_version=draft.prompt_version,
        created_at=draft.created_at,
        plan=draft.plan,
        stages=report.stages,
        issues=[
            PlanIssueResponse(
                code=i.code, severity=i.severity, message=i.message, task_keys=list(i.task_keys)
            )
            for i in report.issues
        ],
    )


@router.post("/{workflow_id}/plan-drafts/{version}/confirm")
async def confirm_plan_draft(
    workflow_id: uuid.UUID,
    version: Annotated[int, Path(ge=1)],
    body: PlanConfirmRequest,
    access: Member,
    session: DbSession,
) -> WorkflowResponse:
    """Turns a plan draft into the workflow's tasks and dependencies (Step 2.5). Requires the
    workflow's `revision`; drafts with errors are refused and warnings must be acknowledged."""
    workflow = await task_service.confirm_plan_draft(
        session,
        access,
        workflow_id,
        version,
        revision=body.revision,
        existing_tasks=body.existing_tasks,
        acknowledge_warnings=body.acknowledge_warnings,
    )
    return service.workflow_response(workflow)


@router.post("/{workflow_id}/plan-drafts")
async def regenerate_plan(
    workflow_id: uuid.UUID, access: Member, session: DbSession
) -> WorkflowResponse:
    """Requests a new plan draft from the current analysis. It runs in a worker."""
    return service.workflow_response(await service.regenerate_plan(session, access, workflow_id))


@router.post("/{workflow_id}/start")
async def start_workflow(
    workflow_id: uuid.UUID, body: RevisionReasonRequest, access: Member, session: DbSession
) -> WorkflowResponse:
    """READY -> RUNNING (handoff endpoint)."""
    workflow = await service.user_transition(
        session,
        access,
        workflow_id,
        target=WorkflowStatus.RUNNING,
        revision=body.revision,
        reason=body.reason,
    )
    return service.workflow_response(workflow)


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: uuid.UUID, body: RevisionReasonRequest, access: Member, session: DbSession
) -> WorkflowResponse:
    """Cancels the workflow and every unfinished task (handoff endpoint)."""
    workflow = await service.user_transition(
        session,
        access,
        workflow_id,
        target=WorkflowStatus.CANCELLED,
        revision=body.revision,
        reason=body.reason,
    )
    return service.workflow_response(workflow)


@router.get("/{workflow_id}/events")
async def list_events(
    workflow_id: uuid.UUID,
    access: Viewer,
    session: DbSession,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EventPage:
    """Append-only history in order. Continue with `after=<last_sequence>`."""
    events = await service.list_events(
        session, access, workflow_id, after_sequence=after, limit=limit
    )
    items = [WorkflowEventResponse.model_validate(e, from_attributes=True) for e in events]
    return EventPage(items=items, last_sequence=items[-1].sequence if items else None)


# --- tasks -----------------------------------------------------------------------------------


@router.get("/{workflow_id}/tasks")
async def list_tasks(
    workflow_id: uuid.UUID,
    access: Viewer,
    session: DbSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[TaskResponse]:
    workflow = await service.get_workflow(session, access, workflow_id)
    tasks, total, deps = await task_service.list_tasks(
        session, workflow, limit=limit, offset=offset
    )
    items = [_task(t, deps.get(t.id, [])) for t in tasks]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/{workflow_id}/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    workflow_id: uuid.UUID, body: TaskCreateRequest, access: Member, session: DbSession
) -> TaskResponse:
    _, task = await task_service.create_task(session, access, workflow_id, body)
    return await _task_response(session, access, workflow_id, task)


@router.get("/{workflow_id}/tasks/{task_id}")
async def get_task(
    workflow_id: uuid.UUID, task_id: uuid.UUID, access: Viewer, session: DbSession
) -> TaskResponse:
    workflow = await service.get_workflow(session, access, workflow_id)
    task = await task_service.get_task(session, workflow, task_id)
    return await _task_response(session, access, workflow_id, task)


@router.patch("/{workflow_id}/tasks/{task_id}")
async def update_task(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdateRequest,
    access: Member,
    session: DbSession,
) -> TaskResponse:
    task = await task_service.update_task(session, access, workflow_id, task_id, body)
    return await _task_response(session, access, workflow_id, task)


@router.delete("/{workflow_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_task(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    access: Member,
    session: DbSession,
    revision: Annotated[int, Query(ge=1)],
) -> Response:
    """Removes the task from the plan (kept as CANCELLED history)."""
    await task_service.remove_task(session, access, workflow_id, task_id, revision=revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workflow_id}/tasks/{task_id}/dependencies", status_code=status.HTTP_201_CREATED)
async def add_dependency(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    body: DependencyRequest,
    access: Member,
    session: DbSession,
) -> TaskResponse:
    task = await task_service.add_dependency(
        session, access, workflow_id, task_id, body.depends_on_task_id
    )
    return await _task_response(session, access, workflow_id, task)


@router.delete(
    "/{workflow_id}/tasks/{task_id}/dependencies/{depends_on_task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_dependency(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    depends_on_task_id: uuid.UUID,
    access: Member,
    session: DbSession,
) -> Response:
    await task_service.remove_dependency(session, access, workflow_id, task_id, depends_on_task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workflow_id}/tasks/{task_id}/assign")
async def assign_agent(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    body: AssignRequest,
    access: Member,
    session: DbSession,
) -> TaskResponse:
    """Assigns an ACTIVE agent; the task pins that agent's current published version."""
    task = await task_service.assign_agent(
        session, access, workflow_id, task_id, agent_id=body.agent_id, revision=body.revision
    )
    return await _task_response(session, access, workflow_id, task)


@router.get("/{workflow_id}/tasks/{task_id}/executions")
async def list_executions(
    workflow_id: uuid.UUID, task_id: uuid.UUID, access: Viewer, session: DbSession
) -> ExecutionList:
    """Every attempt of the task, oldest first, with the exact agent version it used."""
    workflow = await service.get_workflow(session, access, workflow_id)
    await task_service.get_task(session, workflow, task_id)  # 404 for unknown/foreign tasks
    rows = await repository.list_executions(session, workflow, task_id)
    items = [
        ExecutionResponse(
            id=e.id,
            task_id=e.task_id,
            attempt_number=e.attempt_number,
            status=e.status,
            agent_id=e.agent_id,
            agent_version_id=e.agent_version_id,
            agent_version=version,
            started_at=e.started_at,
            completed_at=e.completed_at,
            error_code=e.error_code,
            error_summary=e.error_summary,
            result_summary=e.result_summary,
            submission=e.submission,
        )
        for e, version in rows
    ]
    return ExecutionList(items=items)


@router.post("/{workflow_id}/tasks/{task_id}/cancel")
async def cancel_task(
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    body: RevisionReasonRequest,
    access: Member,
    session: DbSession,
) -> TaskResponse:
    task = await task_service.cancel_task(
        session, access, workflow_id, task_id, revision=body.revision, reason=body.reason
    )
    return await _task_response(session, access, workflow_id, task)
