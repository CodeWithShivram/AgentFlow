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
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.selection import Candidate
from app.core.errors import AppError
from app.models import (
    AgentProposal,
    AgentSelection,
    TaskDependency,
    TaskStatus,
    Workflow,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
from app.planning.dag import analyse_plan
from app.planning.schema import PlanProposal
from app.repositories import agents as agents_repository
from app.repositories import workflows as repository
from app.schemas.agents import AgentConfiguration
from app.schemas.workflows import TaskCreateRequest, TaskUpdateRequest
from app.services import agents as agent_service
from app.services import selection as selection_service
from app.services.authorization import WorkspaceAccess
from app.services.transitions import record_event, transition_task, transition_workflow
from app.services.workflows import PlanDraftNotFoundError, get_workflow, lock_for_change
from app.workflows.state_machine import PLAN_EDIT_REOPENS, PLAN_EDITABLE, Actor

T, W = TaskStatus, WorkflowStatus
MAX_TASKS_PER_WORKFLOW = 200
MAX_DEPENDENCIES_PER_TASK = 50
ExistingTasksMode = Literal["replace", "append"]


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


class PlanDraftInvalidError(AppError):
    status_code = 409
    code = "PLAN_DRAFT_INVALID"
    message = "This plan draft has errors and cannot be confirmed."


class PlanWarningsNotAcknowledgedError(AppError):
    status_code = 409
    code = "PLAN_WARNINGS_NOT_ACKNOWLEDGED"
    message = "This plan draft has warnings; review and acknowledge them to confirm it."


class ExistingTasksModeRequiredError(AppError):
    status_code = 409
    code = "EXISTING_TASKS_MODE_REQUIRED"
    message = "The workflow already has tasks; choose whether to replace them or add the draft."


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


async def _remove_from_plan(
    session: AsyncSession, workflow: Workflow, task: WorkflowTask, *, reason: str
) -> None:
    """Removes a task from the plan, keeping it as CANCELLED history without dependency edges."""
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
        reason=reason,
        event_type=WorkflowEventType.TASK_REMOVED,
        extra={"removed_dependency_edges": removed_edges},
        reconcile=False,
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
        deliverables=list(request.deliverables),
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
        if name in ("acceptance_criteria", "deliverables") and value is not None:
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
    await _remove_from_plan(session, workflow, task, reason="removed from plan")
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
    # The rationale is recomputed here by the server rules (ADR-0023) and stored with the
    # assignment, including when the member picked someone other than the top suggestion.
    ranked = await selection_service.rank_for_task(
        session, workspace_id=access.workspace_id, task=task
    )
    selection = selection_service.record_selection(
        session,
        workflow,
        task,
        ranked,
        agent_id=agent_id,
        agent_version_id=version.id,
        user_id=access.user_id,
    )
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
            "selection_id": str(selection.id),
            "selection_rank": selection.rank,
        },
    )
    await session.commit()
    return task


async def suggest_agents(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> list[Candidate]:
    """Every ACTIVE agent ranked for the task by the selection rules (ADR-0023). Read-only."""
    workflow = await get_workflow(session, access, workflow_id)
    task = await _get_task(session, workflow, task_id)
    return await selection_service.rank_for_task(
        session, workspace_id=access.workspace_id, task=task
    )


async def list_agent_selections(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> list[AgentSelection]:
    """The stored rationales of the task's assignments, newest first."""
    workflow = await get_workflow(session, access, workflow_id)
    await _get_task(session, workflow, task_id)  # 404 for unknown or foreign tasks
    return await repository.list_agent_selections(session, workflow, task_id)


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


async def confirm_plan_draft(
    session: AsyncSession,
    access: WorkspaceAccess,
    workflow_id: uuid.UUID,
    version: int,
    *,
    revision: int,
    existing_tasks: ExistingTasksMode | None,
    acknowledge_warnings: bool,
) -> Workflow:
    """Turns a plan draft into the workflow's tasks and dependencies (Step 2.5, ADR-0017).

    One transaction under the workflow lock and the caller's `revision`. Drafts with errors are
    refused; warnings must be acknowledged. Existing tasks are replaced (kept as removed
    history) or kept alongside, as the user chooses. Like any plan edit, confirming in
    AWAITING_APPROVAL or READY sends the workflow back to PLANNING.
    """
    workflow = await lock_for_change(session, access, workflow_id, revision)
    draft = await repository.get_plan_draft(session, workflow, version)
    if draft is None:
        raise PlanDraftNotFoundError()
    proposal = PlanProposal.model_validate(draft.plan)
    skills = await agents_repository.active_capabilities(session, workspace_id=access.workspace_id)
    report = analyse_plan(
        proposal.tasks, max_parallel_tasks=workflow.max_parallel_tasks, available_skills=skills
    )
    if report.has_errors:
        raise PlanDraftInvalidError(details={"codes": [i.code for i in report.issues]})
    warnings = sorted({i.code for i in report.issues if i.severity == "warning"})
    if warnings and not acknowledge_warnings:
        raise PlanWarningsNotAcknowledgedError(details={"codes": warnings})
    active = [t for t in await repository.list_tasks(session, workflow) if t.removed_at is None]
    if active and existing_tasks is None:
        raise ExistingTasksModeRequiredError(details={"existing_tasks": len(active)})

    await _open_plan_for_edit(session, workflow)
    removed = 0
    if active and existing_tasks == "replace":
        for task in active:
            await _remove_from_plan(session, workflow, task, reason="replaced by plan draft")
            removed += 1
    total = await repository.count_tasks(session, workflow.id) + len(proposal.tasks)
    if total > MAX_TASKS_PER_WORKFLOW:
        raise PlanLimitError(f"A workflow may have at most {MAX_TASKS_PER_WORKFLOW} tasks.")

    ids: dict[str, uuid.UUID] = {}
    created: list[WorkflowTask] = []
    for planned in proposal.tasks:
        task = WorkflowTask(
            id=uuid.uuid4(),
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            title=planned.title,
            description=planned.description,
            required_skills=list(planned.required_skills),
            input_references=[],
            expected_output_schema=None,
            acceptance_criteria=[c.model_dump() for c in planned.acceptance_criteria],
            deliverables=list(planned.deliverables),
            priority=planned.priority,
            required=planned.required,
            status=T.PENDING,
            revision=1,
        )
        session.add(task)
        created.append(task)
        ids[planned.key] = task.id
    await session.flush()
    for planned in proposal.tasks:
        record_event(
            session,
            workflow,
            WorkflowEventType.TASK_CREATED,
            task_id=ids[planned.key],
            payload={
                "actor": Actor.USER.value,
                "user_id": str(access.user_id),
                "plan_version": version,
                "plan_task_key": planned.key,
            },
        )
    # New tasks depend only on new tasks, and drafts are acyclic, so no cycle can form.
    for planned in proposal.tasks:
        for key in planned.depends_on:
            session.add(
                TaskDependency(
                    task_id=ids[planned.key], depends_on_task_id=ids[key], workflow_id=workflow.id
                )
            )
            record_event(
                session,
                workflow,
                WorkflowEventType.DEPENDENCY_ADDED,
                task_id=ids[planned.key],
                payload={"depends_on_task_id": str(ids[key])},
            )
    proposals = (
        await _propose_for_unassignable(session, workflow, created)
        if workflow.allow_dynamic_agent_proposals
        else []
    )
    workflow.confirmed_plan_version = version
    record_event(
        session,
        workflow,
        WorkflowEventType.PLAN_CONFIRMED,
        payload={
            "actor": Actor.USER.value,
            "user_id": str(access.user_id),
            "version": version,
            "existing_tasks": existing_tasks,
            "tasks_created": len(proposal.tasks),
            "tasks_removed": removed,
            "acknowledged_warnings": warnings,
            "agent_proposals_created": len(proposals),
        },
    )
    workflow.revision += 1
    await session.commit()
    return workflow


async def _propose_for_unassignable(
    session: AsyncSession, workflow: Workflow, tasks: list[WorkflowTask]
) -> list[AgentProposal]:
    """Platform proposals (ADR-0024) for confirmed tasks that no active agent can take, one per
    distinct skill set. Only for workflows that allow dynamic agent proposals."""
    gaps: list[agent_service.Gap] = []
    seen: set[frozenset[str]] = set()
    for task in tasks:
        skills = frozenset(task.required_skills)
        if not skills or skills in seen:
            continue
        seen.add(skills)
        ranked = await selection_service.rank_for_task(
            session, workspace_id=workflow.workspace_id, task=task
        )
        if not any(candidate.eligible for candidate in ranked):
            gaps.append(
                agent_service.Gap(task_id=task.id, task_title=task.title, skills=sorted(skills))
            )
    return await agent_service.propose_specialists(
        session, workflow_id=workflow.id, workspace_id=workflow.workspace_id, gaps=gaps
    )
