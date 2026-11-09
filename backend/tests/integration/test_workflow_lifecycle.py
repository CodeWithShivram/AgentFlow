"""Workflow/task lifecycle against real PostgreSQL, driven through the services with the actor
classes that later steps will use (scheduler, worker, verifier), plus concurrency checks."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.agents.seed import seed_builtin_agents
from app.models import (
    Agent,
    AutonomyPolicy,
    TaskStatus,
    User,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.repositories import workflows as repository
from app.schemas.workflows import TaskCreateRequest, WorkflowCreateRequest
from app.services import tasks as task_service
from app.services import workflows as workflow_service
from app.services.authorization import WorkspaceAccess
from app.services.idempotency import IdempotencyKeyReusedError, IdempotencyService
from app.services.transitions import (
    InvalidTransitionError,
    TransitionPreconditionError,
    transition_task,
    transition_workflow,
)
from app.workflows.state_machine import Actor

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
# Sessions mirror production (app.db.session.create_session_factory): no autoflush.

T, W = TaskStatus, WorkflowStatus
IDEMPOTENCY = IdempotencyService(ttl=timedelta(hours=1))


@dataclass(frozen=True)
class World:
    owner: WorkspaceAccess
    writer_agent_id: uuid.UUID


async def _run[R](
    engine: AsyncEngine, fn: Callable[..., Awaitable[R]], *args: Any, **kwargs: Any
) -> R:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        return await fn(session, *args, **kwargs)


@pytest.fixture
async def world(engine: AsyncEngine) -> World:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        await seed_builtin_agents(session)
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h")
        session.add(user)
        await session.flush()
        workspace = Workspace(owner_id=user.id, name="W")
        session.add(workspace)
        await session.flush()
        session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
        )
        writer = (
            await session.execute(
                select(Agent.id).where(Agent.name == "Writer", Agent.workspace_id.is_(None))
            )
        ).scalar_one()
        await session.commit()
    access = WorkspaceAccess(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
    return World(owner=access, writer_agent_id=writer)


async def _workflow(
    engine: AsyncEngine, world: World, autonomy: AutonomyPolicy = AutonomyPolicy.AUTONOMOUS
) -> uuid.UUID:
    request = WorkflowCreateRequest.model_validate(
        {"prompt": "Write a report", "settings": {"autonomy_policy": autonomy.value}}
    )
    created = await _run(
        engine,
        workflow_service.create_workflow,
        world.owner,
        request,
        idempotency=IDEMPOTENCY,
        idempotency_key=None,
    )
    return uuid.UUID(created.body["id"])


async def _task(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID, title: str, **fields: Any
) -> uuid.UUID:
    request = TaskCreateRequest.model_validate(
        {"title": title, "required_skills": ["report_writing"], **fields}
    )
    _, task = await _run(engine, task_service.create_task, world.owner, workflow_id, request)
    return task.id


async def _move(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID, *targets: WorkflowStatus
) -> None:
    for target in targets:
        workflow = await _run(engine, workflow_service.get_workflow, world.owner, workflow_id)
        await _run(
            engine,
            workflow_service.user_transition,
            world.owner,
            workflow_id,
            target=target,
            revision=workflow.revision,
            reason=None,
        )


async def _task_step(
    engine: AsyncEngine,
    world: World,
    workflow_id: uuid.UUID,
    task_id: uuid.UUID,
    target: TaskStatus,
    actor: Actor,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        workflow = await repository.lock_workflow(
            session, workspace_id=world.owner.workspace_id, workflow_id=workflow_id
        )
        assert workflow is not None
        task = await repository.get_task(session, workflow, task_id)
        assert task is not None
        await transition_task(session, workflow, task, target, actor=actor)
        await session.commit()


async def _assign(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> WorkflowTask:
    task = await _status_of(engine, world, workflow_id, task_id)
    return await _run(
        engine,
        task_service.assign_agent,
        world.owner,
        workflow_id,
        task_id,
        agent_id=world.writer_agent_id,
        revision=task.revision,
    )


async def _execute_to_completion(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> None:
    await _assign(engine, world, workflow_id, task_id)
    for target, actor in (
        (T.QUEUED, Actor.SCHEDULER),
        (T.IN_PROGRESS, Actor.WORKER),
        (T.SUBMITTED, Actor.WORKER),
        (T.VERIFYING, Actor.VERIFIER),
        (T.COMPLETED, Actor.VERIFIER),
    ):
        await _task_step(engine, world, workflow_id, task_id, target, actor)


async def _status_of(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> WorkflowTask:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        workflow = await repository.get_workflow(
            session, workspace_id=world.owner.workspace_id, workflow_id=workflow_id
        )
        assert workflow is not None
        task = await repository.get_task(session, workflow, task_id)
        assert task is not None
        return task


async def _workflow_status(
    engine: AsyncEngine, world: World, workflow_id: uuid.UUID
) -> WorkflowStatus:
    return (await _run(engine, workflow_service.get_workflow, world.owner, workflow_id)).status


async def _events(engine: AsyncEngine, workflow_id: uuid.UUID) -> list[WorkflowEvent]:
    async with AsyncSession(engine) as session:
        statement = (
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_id == workflow_id)
            .order_by(WorkflowEvent.sequence)
        )
        return list((await session.execute(statement)).scalars())


async def _running_with(
    engine: AsyncEngine, world: World, *task_specs: dict[str, Any]
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    workflow_id = await _workflow(engine, world)
    ids = [
        await _task(engine, world, workflow_id, f"t{i}", **spec)
        for i, spec in enumerate(task_specs)
    ]
    await _move(engine, world, workflow_id, W.PLANNING, W.READY, W.RUNNING)
    return workflow_id, ids


# --- happy path and propagation --------------------------------------------------------------


async def test_dependent_tasks_wait_and_workflow_completes_when_required_tasks_do(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id = await _workflow(engine, world)
    first = await _task(engine, world, workflow_id, "research")
    second = await _task(engine, world, workflow_id, "write", depends_on=[str(first)])
    optional = await _task(engine, world, workflow_id, "polish", required=False)
    await _move(engine, world, workflow_id, W.PLANNING, W.READY, W.RUNNING)

    assert (await _status_of(engine, world, workflow_id, first)).status == T.READY
    assert (await _status_of(engine, world, workflow_id, second)).status == T.PENDING
    with pytest.raises(TransitionPreconditionError):  # dependencies not completed
        await _task_step(engine, world, workflow_id, second, T.READY, Actor.SYSTEM)

    await _execute_to_completion(engine, world, workflow_id, first)
    assert (await _status_of(engine, world, workflow_id, second)).status == T.READY
    assert await _workflow_status(engine, world, workflow_id) == W.RUNNING

    await _execute_to_completion(engine, world, workflow_id, second)

    assert await _workflow_status(engine, world, workflow_id) == W.COMPLETED
    assert (await _status_of(engine, world, workflow_id, optional)).status == T.CANCELLED
    types = [e.event_type for e in await _events(engine, workflow_id)]
    assert types[0] == WorkflowEventType.WORKFLOW_CREATED
    assert types[-2:] == [
        WorkflowEventType.WORKFLOW_COMPLETED,
        WorkflowEventType.TASK_STATUS_CHANGED,
    ]


async def test_agents_cannot_mark_their_own_task_completed(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id, (task_id,) = await _running_with(engine, world, {})
    await _assign(engine, world, workflow_id, task_id)
    await _task_step(engine, world, workflow_id, task_id, T.QUEUED, Actor.SCHEDULER)
    await _task_step(engine, world, workflow_id, task_id, T.IN_PROGRESS, Actor.WORKER)

    with pytest.raises(InvalidTransitionError):
        await _task_step(engine, world, workflow_id, task_id, T.COMPLETED, Actor.WORKER)
    await _task_step(engine, world, workflow_id, task_id, T.SUBMITTED, Actor.WORKER)
    await _task_step(engine, world, workflow_id, task_id, T.VERIFYING, Actor.VERIFIER)
    with pytest.raises(InvalidTransitionError):
        await _task_step(engine, world, workflow_id, task_id, T.COMPLETED, Actor.WORKER)


async def test_workflow_cannot_complete_with_unresolved_required_tasks(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id, _ = await _running_with(engine, world, {}, {})

    async with AsyncSession(engine) as session:
        workflow = await repository.lock_workflow(
            session, workspace_id=world.owner.workspace_id, workflow_id=workflow_id
        )
        assert workflow is not None
        with pytest.raises(TransitionPreconditionError) as exc_info:
            await transition_workflow(session, workflow, W.COMPLETED, actor=Actor.SYSTEM)
    assert len(exc_info.value.details["unresolved_task_ids"]) == 2
    assert await _workflow_status(engine, world, workflow_id) == W.RUNNING


async def test_failed_required_task_fails_the_workflow_and_cancels_the_rest(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id, (doomed, other) = await _running_with(engine, world, {}, {})
    await _assign(engine, world, workflow_id, doomed)
    await _task_step(engine, world, workflow_id, doomed, T.QUEUED, Actor.SCHEDULER)
    await _task_step(engine, world, workflow_id, doomed, T.IN_PROGRESS, Actor.WORKER)

    await _task_step(engine, world, workflow_id, doomed, T.FAILED, Actor.SYSTEM)

    assert await _workflow_status(engine, world, workflow_id) == W.FAILED
    assert (await _status_of(engine, world, workflow_id, other)).status == T.CANCELLED


async def test_escalation_blocks_until_the_task_is_reassigned(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id, (task_id,) = await _running_with(engine, world, {})
    await _assign(engine, world, workflow_id, task_id)
    await _task_step(engine, world, workflow_id, task_id, T.QUEUED, Actor.SCHEDULER)
    await _task_step(engine, world, workflow_id, task_id, T.IN_PROGRESS, Actor.WORKER)
    await _task_step(engine, world, workflow_id, task_id, T.ESCALATED, Actor.SYSTEM)
    assert await _workflow_status(engine, world, workflow_id) == W.BLOCKED

    with pytest.raises(TransitionPreconditionError):
        await _move(engine, world, workflow_id, W.RUNNING)
    await _assign(engine, world, workflow_id, task_id)  # reassignment is allowed while blocked
    with pytest.raises(TransitionPreconditionError):  # but no dispatch while blocked
        await _task_step(engine, world, workflow_id, task_id, T.QUEUED, Actor.SCHEDULER)
    await _move(engine, world, workflow_id, W.RUNNING)

    assert await _workflow_status(engine, world, workflow_id) == W.RUNNING
    events = await _events(engine, workflow_id)
    assert WorkflowEventType.TASK_REASSIGNED in [e.event_type for e in events]


async def test_retry_keeps_history_and_counts_attempts(engine: AsyncEngine, world: World) -> None:
    workflow_id, (task_id,) = await _running_with(engine, world, {})
    first_assignment = await _assign(engine, world, workflow_id, task_id)
    for target, actor in (
        (T.QUEUED, Actor.SCHEDULER),
        (T.IN_PROGRESS, Actor.WORKER),
        (T.SUBMITTED, Actor.WORKER),
        (T.VERIFYING, Actor.VERIFIER),
        (T.REWORK_REQUIRED, Actor.VERIFIER),
    ):
        await _task_step(engine, world, workflow_id, task_id, target, actor)

    retried = await _assign(engine, world, workflow_id, task_id)

    assert retried.attempt_count == 1
    assert retried.assigned_agent_version_id == first_assignment.assigned_agent_version_id
    events = [e for e in await _events(engine, workflow_id) if e.task_id == task_id]
    types = [e.event_type for e in events]
    assert WorkflowEventType.VERIFICATION_FAILED in types
    reassigned = next(e for e in events if e.event_type == WorkflowEventType.TASK_REASSIGNED)
    assert reassigned.payload["previous_agent_version_id"] == str(
        first_assignment.assigned_agent_version_id
    )
    assert reassigned.payload["attempt_count"] == 1


async def test_dependent_of_a_cancelled_task_blocks_the_workflow(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id = await _workflow(engine, world)
    base = await _task(engine, world, workflow_id, "optional base", required=False)
    await _task(engine, world, workflow_id, "needs base", depends_on=[str(base)])
    await _move(engine, world, workflow_id, W.PLANNING, W.READY, W.RUNNING)
    task = await _status_of(engine, world, workflow_id, base)

    await _run(
        engine,
        task_service.cancel_task,
        world.owner,
        workflow_id,
        base,
        revision=task.revision,
        reason="not needed",
    )

    assert await _workflow_status(engine, world, workflow_id) == W.BLOCKED


# --- atomicity --------------------------------------------------------------------------------


async def test_rejected_transition_changes_nothing_and_writes_no_event(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id = await _workflow(engine, world, AutonomyPolicy.APPROVAL_REQUIRED)
    await _move(engine, world, workflow_id, W.PLANNING)
    before = await _events(engine, workflow_id)

    with pytest.raises(TransitionPreconditionError):  # empty plan
        await _move(engine, world, workflow_id, W.AWAITING_APPROVAL)

    assert await _workflow_status(engine, world, workflow_id) == W.PLANNING
    assert len(await _events(engine, workflow_id)) == len(before)


async def test_every_status_change_has_exactly_one_event(engine: AsyncEngine, world: World) -> None:
    workflow_id, (task_id,) = await _running_with(engine, world, {})
    await _execute_to_completion(engine, world, workflow_id, task_id)

    events = await _events(engine, workflow_id)
    workflow_changes = [e for e in events if e.task_id is None and "to" in e.payload]
    task_changes = [e for e in events if e.task_id == task_id and "to" in e.payload]

    assert [e.payload["to"] for e in workflow_changes] == [
        "PLANNING",
        "READY",
        "RUNNING",
        "COMPLETED",
    ]
    assert [e.payload["to"] for e in task_changes] == [
        "READY",
        "ASSIGNED",
        "QUEUED",
        "IN_PROGRESS",
        "SUBMITTED",
        "VERIFYING",
        "COMPLETED",
    ]
    assert [e.sequence for e in events] == sorted(e.sequence for e in events)


# --- concurrency -------------------------------------------------------------------------------


async def _gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.gather(*coroutines, return_exceptions=True))


async def test_concurrent_transitions_with_one_revision_apply_once(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id = await _workflow(engine, world)

    results = await _gather(
        *(
            _run(
                engine,
                workflow_service.user_transition,
                world.owner,
                workflow_id,
                target=target,
                revision=1,
                reason=None,
            )
            for target in (W.PLANNING, W.CANCELLED, W.PLANNING, W.CANCELLED)
        )
    )

    winners = [r for r in results if isinstance(r, Workflow)]
    assert len(winners) == 1
    assert all(
        isinstance(r, workflow_service.WorkflowRevisionConflictError)
        for r in results
        if r not in winners
    )
    assert len([e for e in await _events(engine, workflow_id) if "to" in e.payload]) == 1


async def test_concurrent_opposite_dependencies_cannot_form_a_cycle(
    engine: AsyncEngine, world: World
) -> None:
    workflow_id = await _workflow(engine, world)
    a = await _task(engine, world, workflow_id, "a")
    b = await _task(engine, world, workflow_id, "b")

    results = await _gather(
        _run(engine, task_service.add_dependency, world.owner, workflow_id, a, b),
        _run(engine, task_service.add_dependency, world.owner, workflow_id, b, a),
    )

    assert sum(isinstance(r, WorkflowTask) for r in results) == 1
    assert sum(isinstance(r, task_service.DependencyCycleError) for r in results) == 1


async def test_concurrent_idempotent_creates_make_one_workflow(
    engine: AsyncEngine, world: World
) -> None:
    request = WorkflowCreateRequest.model_validate({"prompt": "Same request"})

    results = await _gather(
        *(
            _run(
                engine,
                workflow_service.create_workflow,
                world.owner,
                request,
                idempotency=IDEMPOTENCY,
                idempotency_key="create-1",
            )
            for _ in range(6)
        )
    )

    assert all(isinstance(r, workflow_service.CreatedWorkflow) for r in results)
    assert sum(not r.replayed for r in results) == 1
    assert len({r.body["id"] for r in results}) == 1
    async with AsyncSession(engine) as session:
        count = (await session.execute(select(func.count()).select_from(Workflow))).scalar_one()
    assert count == 1

    different = WorkflowCreateRequest.model_validate({"prompt": "Different request"})
    with pytest.raises(IdempotencyKeyReusedError):
        await _run(
            engine,
            workflow_service.create_workflow,
            world.owner,
            different,
            idempotency=IDEMPOTENCY,
            idempotency_key="create-1",
        )
