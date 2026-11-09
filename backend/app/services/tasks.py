"""Task CRUD, dependency DAG and agent assignment (ADR-0009).

Every mutation locks the workflow row first, so plan edits, dependency checks and status
changes of one workflow are serialized; the DAG check therefore sees a consistent graph.

Plan rules:
- Tasks and dependencies may change only while the workflow is PENDING, PLANNING,
  AWAITING_APPROVAL or READY; editing in the last two sends the workflow back to PLANNING.
- Dependencies must stay inside one workflow, may not point at a task itself, may not be
  duplicated and may never form a cycle.
- Removing a task keeps the row (events reference it): it becomes CANCELLED, `removed_at` is
  set and its dependency edges are deleted.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    TaskDependency,
    TaskStatus,
    Workflow,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
from app.repositories import workflows as repository
from app.schemas.agents import AgentConfiguration
from app.schemas.workflows import TaskCreateRequest, TaskUpdateRequest
from app.services import agents as agent_service
from app.services.authorization import WorkspaceAccess
from app.services.transitions import record_event, transition_task, transition_workflow
from app.services.workflows import lock_for_change
from app.workflows.state_machine import PLAN_EDIT_REOPENS, PLAN_EDITABLE, Actor

T, W = TaskStatus, WorkflowStatus
MAX_TASKS_PER_WORKFLOW = 200
MAX_DEPENDENCIES_PER_TASK = 50


class TaskNotFoundError(AppError):
    status_code = 404
    code = "TASK_NOT_FOUND"
    message = "Task not found."


class TaskRevisionConflictError(AppError):
    status_code = 409
    code = "TASK_REVISION_CONFLICT"
    message = "The task was changed by someone else. Reload it and try again."


class PlanNotEditableError(AppError):
    status_code = 409
    code = "PLAN_NOT_EDITABLE"
    message = "Tasks can only be changed while the workflow is being planned."


class InvalidDependencyError(AppError):
    status_code = 422
    code = "INVALID_DEPENDENCY"
    message = "A dependency must be another active task in the same workflow."


class DependencyCycleError(AppError):
    status_code = 422
    code = "DEPENDENCY_CYCLE"
    message = "This dependency would create a cycle."


class DependencyExistsError(AppError):
    status_code = 409
    code = "DEPENDENCY_EXISTS"
    message = "This dependency already exists."


class PlanLimitError(AppError):
    status_code = 422
    code = "PLAN_LIMIT_EXCEEDED"
    message = "The plan is too large."


class AgentLacksSkillsError(AppError):
    status_code = 422
    code = "AGENT_LACKS_SKILLS"
    message = "The agent does not have every skill this task requires."


# --- helpers ---------------------------------------------------------------------------------


async def _open_plan_for_edit(session: AsyncSession, workflow: Workflow) -> None:
    if workflow.status not in PLAN_EDITABLE:
        raise PlanNotEditableError()
    if workflow.status in PLAN_EDIT_REOPENS:
        # An approved or submitted plan is being changed: it must be submitted again.
        await transition_workflow(
            session, workflow, W.PLANNING, actor=Actor.USER, reason="plan edited"
        )


async def _get_task(
    session: AsyncSession, workflow: Workflow, task_id: uuid.UUID, revision: int | None = None
) -> WorkflowTask:
    task = await repository.get_task(session, workflow, task_id)
    if task is None:
        raise TaskNotFoundError()
    if revision is not None and task.revision != revision:
        raise TaskRevisionConflictError(details={"current_revision": task.revision})
    return task


def would_create_cycle(
    edges: list[tuple[uuid.UUID, uuid.UUID]], task_id: uuid.UUID, depends_on: uuid.UUID
) -> bool:
    """True if `task_id -> depends_on` closes a cycle, i.e. `depends_on` already (transitively)
    depends on `task_id`. Edges are (task, depends_on) pairs."""
    graph: dict[uuid.UUID, list[uuid.UUID]] = {}
    for source, target in edges:
        graph.setdefault(source, []).append(target)
    stack, seen = [depends_on], set()
    while stack:
        node = stack.pop()
        if node == task_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False


async def _add_dependency(
    session: AsyncSession, workflow: Workflow, task: WorkflowTask, depends_on: uuid.UUID
) -> None:
    if depends_on == task.id:
        raise InvalidDependencyError("A task cannot depend on itself.")
    target = await repository.get_task(session, workflow, depends_on)
    # Unknown IDs, tasks of other workflows and other workspaces all fail identically.
    if target is None or target.removed_at is not None or task.removed_at is not None:
        raise InvalidDependencyError()
    edges = await repository.list_dependencies(session, workflow.id)
    if (task.id, depends_on) in edges:
        raise DependencyExistsError()
    if sum(1 for source, _ in edges if source == task.id) >= MAX_DEPENDENCIES_PER_TASK:
        raise PlanLimitError(f"A task may have at most {MAX_DEPENDENCIES_PER_TASK} dependencies.")
    if would_create_cycle(edges, task.id, depends_on):
        raise DependencyCycleError()
    session.add(
        TaskDependency(task_id=task.id, depends_on_task_id=depends_on, workflow_id=workflow.id)
    )
    await session.flush()
    record_event(
        session,
        workflow,
        WorkflowEventType.DEPENDENCY_ADDED,
        task_id=task.id,
        payload={"depends_on_task_id": str(depends_on)},
    )


# --- reads -----------------------------------------------------------------------------------


async def list_tasks(
    session: AsyncSession, workflow: Workflow, *, limit: int, offset: int
) -> tuple[list[WorkflowTask], int, dict[uuid.UUID, list[uuid.UUID]]]:
    tasks, total = await repository.page_tasks(session, workflow, limit=limit, offset=offset)
    return tasks, total, await dependency_map(session, workflow)


async def dependency_map(
    session: AsyncSession, workflow: Workflow
) -> dict[uuid.UUID, list[uuid.UUID]]:
    result: dict[uuid.UUID, list[uuid.UUID]] = {}
    for task_id, depends_on in await repository.list_dependencies(session, workflow.id):
        result.setdefault(task_id, []).append(depends_on)
    return result


async def get_task(session: AsyncSession, workflow: Workflow, task_id: uuid.UUID) -> WorkflowTask:
    return await _get_task(session, workflow, task_id)


# --- plan edits ------------------------------------------------------------------------------


async def create_task(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    request: TaskCreateRequest,
) -> tuple[Workflow, WorkflowTask]:
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    await _open_plan_for_edit(session, workflow)
    if await repository.count_tasks(session, workflow.id) >= MAX_TASKS_PER_WORKFLOW:
        raise PlanLimitError(f"A workflow may have at most {MAX_TASKS_PER_WORKFLOW} tasks.")
    task = WorkflowTask(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        title=request.title,
        description=request.description,
        required_skills=list(request.required_skills),
        input_references=list(request.input_references),
        expected_output_schema=request.expected_output_schema,
        acceptance_criteria=[c.model_dump() for c in request.acceptance_criteria],
        priority=request.priority,
        required=request.required,
        status=T.PENDING,
        revision=1,
    )
    session.add(task)
    await session.flush()
    record_event(
        session,
        workflow,
        WorkflowEventType.TASK_CREATED,
        task_id=task.id,
        payload={"actor": Actor.USER.value, "user_id": str(access.user_id)},
    )
    for depends_on in dict.fromkeys(request.depends_on):
        await _add_dependency(session, workflow, task, depends_on)
    workflow.revision += 1
    await session.commit()
    return workflow, task


async def update_task(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    request: TaskUpdateRequest,
) -> WorkflowTask:
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id, request.revision)
    if task.removed_at is not None:
        raise TaskNotFoundError()
    fields = request.model_dump(exclude_unset=True, exclude={"revision"})
    changes: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "acceptance_criteria" and value is not None:
            value = list(value)
        if value is not None or name == "expected_output_schema":
            if getattr(task, name) != value:
                changes[name] = value
    if changes:
        await _open_plan_for_edit(session, workflow)
        for name, value in changes.items():
            setattr(task, name, value)
        task.revision += 1
        workflow.revision += 1
        record_event(
            session,
            workflow,
            WorkflowEventType.TASK_UPDATED,
            task_id=task.id,
            payload={"fields": sorted(changes), "user_id": str(access.user_id)},
        )
    await session.commit()
    return task


async def remove_task(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    revision: int,
) -> None:
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id, revision)
    if task.removed_at is not None:
        raise TaskNotFoundError()
    await _open_plan_for_edit(session, workflow)
    removed_edges = await repository.delete_edges_touching(
        session, workflow_id=workflow.id, task_id=task.id
    )
    task.removed_at = datetime.now(UTC)
    await transition_task(
        session,
        workflow,
        task,
        T.CANCELLED,
        actor=Actor.USER,
        reason="removed from plan",
        event_type=WorkflowEventType.TASK_REMOVED,
        extra={"removed_dependency_edges": removed_edges},
        reconcile=False,
    )
    workflow.revision += 1
    await session.commit()


async def add_dependency(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    depends_on: uuid.UUID,
) -> WorkflowTask:
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id)
    await _open_plan_for_edit(session, workflow)
    await _add_dependency(session, workflow, task, depends_on)
    workflow.revision += 1
    await session.commit()
    return task


async def remove_dependency(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    depends_on: uuid.UUID,
) -> None:
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id)
    await _open_plan_for_edit(session, workflow)
    if not await repository.delete_dependency(
        session, workflow_id=workflow.id, task_id=task.id, depends_on=depends_on
    ):
        raise InvalidDependencyError("This dependency does not exist.")
    record_event(
        session,
        workflow,
        WorkflowEventType.DEPENDENCY_REMOVED,
        task_id=task.id,
        payload={"depends_on_task_id": str(depends_on)},
    )
    workflow.revision += 1
    await session.commit()


# --- execution-time actions ------------------------------------------------------------------


async def assign_agent(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    agent_id: uuid.UUID,
    revision: int,
) -> WorkflowTask:
    """Assigns an ACTIVE agent and pins its current published version on the task.

    Allowed from READY (assign), REWORK_REQUIRED (retry: attempt_count + 1) and ESCALATED
    (manual reassignment). Earlier assignments stay in the event history.
    """
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id, revision)
    version = await agent_service.resolve_runnable_version(
        session, workspace_id=access.workspace_id, agent_id=agent_id
    )
    capabilities = set(AgentConfiguration.model_validate(version.configuration).capabilities)
    missing = sorted(set(task.required_skills) - capabilities)
    if missing:
        raise AgentLacksSkillsError(details={"missing_skills": missing})
    previous = (task.assigned_agent_id, task.assigned_agent_version_id)
    retry = task.status == T.REWORK_REQUIRED
    task.assigned_agent_id = agent_id
    task.assigned_agent_version_id = version.id
    if retry:
        task.attempt_count += 1
    reassigned = previous[0] is not None
    await transition_task(
        session,
        workflow,
        task,
        T.ASSIGNED,
        actor=Actor.USER,
        event_type=WorkflowEventType.TASK_REASSIGNED if reassigned else None,
        extra={
            "agent_id": str(agent_id),
            "agent_version_id": str(version.id),
            "agent_version": version.version,
            "previous_agent_id": str(previous[0]) if previous[0] else None,
            "previous_agent_version_id": str(previous[1]) if previous[1] else None,
            "attempt_count": task.attempt_count,
        },
    )
    await session.commit()
    return task


async def cancel_task(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    revision: int,
    reason: str | None,
) -> WorkflowTask:
    """Cancels a task of a running workflow. During planning, remove the task instead."""
    workflow = await lock_for_change(session, access, workflow_id, revision=None)
    task = await _get_task(session, workflow, task_id, revision)
    if workflow.status in PLAN_EDITABLE:
        raise PlanNotEditableError("Remove the task from the plan instead.")
    await transition_task(session, workflow, task, T.CANCELLED, actor=Actor.USER, reason=reason)
    await session.commit()
    return task
