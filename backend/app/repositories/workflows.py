"""Workflow, task, dependency and event queries. Every query is scoped by workspace_id (and,
for tasks, by workflow_id), so an ID from another workspace or workflow never matches."""

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentExecution,
    AgentVersion,
    TaskDependency,
    Workflow,
    WorkflowEvent,
    WorkflowStatus,
    WorkflowTask,
)


async def list_workflows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    status: WorkflowStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Workflow], int]:
    filters: list[Any] = [Workflow.workspace_id == workspace_id]
    if status is not None:
        filters.append(Workflow.status == status)
    rows = await session.execute(
        select(Workflow)
        .where(*filters)
        .order_by(Workflow.created_at.desc(), Workflow.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(Workflow).where(*filters))
    return list(rows.scalars()), total.scalar_one()


async def get_workflow(
    session: AsyncSession, *, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow | None:
    statement = select(Workflow).where(
        Workflow.id == workflow_id, Workflow.workspace_id == workspace_id
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def lock_workflow(
    session: AsyncSession, *, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow | None:
    """Row lock serializing every change to a workflow, its tasks and its DAG."""
    statement = (
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def list_tasks(session: AsyncSession, workflow: Workflow) -> list[WorkflowTask]:
    statement = (
        select(WorkflowTask)
        .where(
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.workspace_id == workflow.workspace_id,
        )
        .order_by(WorkflowTask.created_at, WorkflowTask.id)
    )
    # No populate_existing: it would overwrite unflushed changes on already-loaded tasks.
    return list((await session.execute(statement)).scalars())


async def page_tasks(
    session: AsyncSession, workflow: Workflow, *, limit: int, offset: int
) -> tuple[list[WorkflowTask], int]:
    scope = (
        WorkflowTask.workflow_id == workflow.id,
        WorkflowTask.workspace_id == workflow.workspace_id,
    )
    rows = await session.execute(
        select(WorkflowTask)
        .where(*scope)
        .order_by(WorkflowTask.created_at, WorkflowTask.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(WorkflowTask).where(*scope))
    return list(rows.scalars()), total.scalar_one()


async def get_task(
    session: AsyncSession, workflow: Workflow, task_id: uuid.UUID
) -> WorkflowTask | None:
    statement = select(WorkflowTask).where(
        WorkflowTask.id == task_id,
        WorkflowTask.workflow_id == workflow.id,
        WorkflowTask.workspace_id == workflow.workspace_id,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def count_tasks(session: AsyncSession, workflow_id: uuid.UUID) -> int:
    statement = select(func.count()).where(WorkflowTask.workflow_id == workflow_id)
    return int((await session.execute(statement)).scalar_one())


async def list_dependencies(
    session: AsyncSession, workflow_id: uuid.UUID
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(task_id, depends_on_task_id) edges of one workflow."""
    rows = await session.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.workflow_id == workflow_id
        )
    )
    return [(row[0], row[1]) for row in rows]


async def delete_dependency(
    session: AsyncSession, *, workflow_id: uuid.UUID, task_id: uuid.UUID, depends_on: uuid.UUID
) -> bool:
    result = await session.execute(
        delete(TaskDependency)
        .where(
            TaskDependency.workflow_id == workflow_id,
            TaskDependency.task_id == task_id,
            TaskDependency.depends_on_task_id == depends_on,
        )
        .returning(TaskDependency.task_id)
    )
    return result.first() is not None


async def delete_edges_touching(
    session: AsyncSession, *, workflow_id: uuid.UUID, task_id: uuid.UUID
) -> int:
    result = await session.execute(
        delete(TaskDependency)
        .where(
            TaskDependency.workflow_id == workflow_id,
            (TaskDependency.task_id == task_id) | (TaskDependency.depends_on_task_id == task_id),
        )
        .returning(TaskDependency.task_id)
    )
    return len(result.all())


async def list_events(
    session: AsyncSession, workflow: Workflow, *, after_sequence: int, limit: int
) -> list[WorkflowEvent]:
    statement = (
        select(WorkflowEvent)
        .where(
            WorkflowEvent.workflow_id == workflow.id,
            WorkflowEvent.workspace_id == workflow.workspace_id,
            WorkflowEvent.sequence > after_sequence,
        )
        .order_by(WorkflowEvent.sequence)
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


async def list_executions(
    session: AsyncSession, workflow: Workflow, task_id: uuid.UUID
) -> list[tuple[AgentExecution, int]]:
    """Attempts of one task, oldest first, with the agent version number each one used."""
    rows = await session.execute(
        select(AgentExecution, AgentVersion.version)
        .join(AgentVersion, AgentVersion.id == AgentExecution.agent_version_id)
        .where(
            AgentExecution.task_id == task_id,
            AgentExecution.workflow_id == workflow.id,
            AgentExecution.workspace_id == workflow.workspace_id,
        )
        .order_by(AgentExecution.attempt_number)
    )
    return [(row[0], row[1]) for row in rows]
