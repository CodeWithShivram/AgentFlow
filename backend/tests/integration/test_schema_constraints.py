"""Database-level integrity rules: constraints, workspace isolation and deletion behaviour.

Each test runs in one transaction that is rolled back afterwards. Expected violations run
inside a SAVEPOINT so the surrounding transaction stays usable.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    Base,
    IdempotencyKey,
    TaskDependency,
    User,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowTask,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _add(session: AsyncSession, *objects: Any) -> None:
    session.add_all(objects)
    await session.flush()


async def _expect_violation(session: AsyncSession, *objects: Any) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _add(session, *objects)


async def _expect_sql_violation(session: AsyncSession, statement: str, **params: Any) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        async with session.begin_nested():
            await session.execute(text(statement), params)


async def _user(session: AsyncSession, email: str | None = None) -> User:
    user = User(email=email or f"{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
    await _add(session, user)
    return user


async def _workspace(session: AsyncSession, owner: User | None = None) -> Workspace:
    owner = owner or await _user(session)
    workspace = Workspace(owner_id=owner.id, name="Workspace")
    await _add(session, workspace)
    return workspace


async def _workflow(session: AsyncSession, workspace: Workspace) -> Workflow:
    workflow = Workflow(
        workspace_id=workspace.id,
        created_by_user_id=workspace.owner_id,
        prompt="Research platforms",
        autonomy_policy="approval_required",
    )
    await _add(session, workflow)
    return workflow


async def _task(session: AsyncSession, workflow: Workflow, **fields: Any) -> WorkflowTask:
    task = WorkflowTask(
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        title="Task",
        description="Do the thing",
        **fields,
    )
    await _add(session, task)
    return task


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- conventions --------------------------------------------------------------------------


def test_every_table_is_workspace_scoped_or_explicitly_exempt() -> None:
    exempt = {
        "users": "global identity; access is through workspace membership",
        "workspaces": "the tenant itself",
        "agent_versions": "scoped through agents.agent_id",
        "task_dependencies": "pinned to one workflow by composite foreign keys",
        "agents": "workspace_id is NULL for built-in agents",
        "auth_sessions": "belongs to a user, not a workspace",
        "refresh_tokens": "belongs to an auth session",
        "agent_audit_events": "workspace_id is NULL for built-in agent history",
    }
    for table in Base.metadata.sorted_tables:
        if table.name in exempt:
            continue
        column = table.columns.get("workspace_id")
        assert column is not None, f"{table.name} lacks workspace_id"
        assert not column.nullable, f"{table.name}.workspace_id must be NOT NULL"


async def test_defaults_uuid_and_utc_timestamps(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    workflow = await _workflow(session, workspace)
    await session.refresh(workflow)
    await session.refresh(workspace)

    assert isinstance(workflow.id, uuid.UUID)
    assert workflow.created_at.utcoffset() == timedelta(0)
    assert workflow.currency == "USD"
    assert workflow.max_parallel_tasks == 3
    assert workflow.status == "PENDING"
    assert workspace.settings == {}


# --- users and membership -----------------------------------------------------------------


async def test_email_is_unique_case_insensitively(session: AsyncSession) -> None:
    await _user(session, "Ada@Example.com")

    await _expect_violation(session, User(email="ada@example.COM", password_hash="h"))


async def test_membership_is_unique_and_role_is_checked(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    member = await _user(session)
    await _add(
        session,
        WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role=WorkspaceRole.MEMBER),
    )

    await _expect_violation(
        session,
        WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role=WorkspaceRole.ADMIN),
    )
    other = await _user(session)
    await _expect_sql_violation(
        session,
        "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (:w, :u, 'ROOT')",
        w=workspace.id,
        u=other.id,
    )


async def test_workspace_owner_cannot_be_deleted(session: AsyncSession) -> None:
    workspace = await _workspace(session)

    await _expect_sql_violation(session, "DELETE FROM users WHERE id = :id", id=workspace.owner_id)


# --- agents -------------------------------------------------------------------------------


async def test_agent_names_unique_per_workspace_including_builtins(session: AsyncSession) -> None:
    first, second = await _workspace(session), await _workspace(session)
    await _add(
        session,
        Agent(workspace_id=None, name="Research", category="research"),
        Agent(workspace_id=first.id, name="Research", category="research"),
        Agent(workspace_id=second.id, name="Research", category="research"),
    )

    await _expect_violation(session, Agent(workspace_id=None, name="Research", category="x"))
    await _expect_violation(session, Agent(workspace_id=first.id, name="Research", category="x"))


async def test_agent_versions_are_numbered_uniquely_from_one(session: AsyncSession) -> None:
    agent = Agent(name="Writer", category="writing")
    await _add(session, agent)
    await _add(session, AgentVersion(agent_id=agent.id, version=1, configuration={}))

    await _expect_violation(session, AgentVersion(agent_id=agent.id, version=1, configuration={}))
    await _expect_violation(session, AgentVersion(agent_id=agent.id, version=0, configuration={}))


async def test_active_version_must_belong_to_the_same_agent(session: AsyncSession) -> None:
    agent, other = Agent(name="A", category="c"), Agent(name="B", category="c")
    await _add(session, agent, other)
    version = AgentVersion(agent_id=agent.id, version=1, configuration={})
    await _add(session, version)

    await session.execute(
        update(Agent).where(Agent.id == agent.id).values(active_version_id=version.id)
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(Agent).where(Agent.id == other.id).values(active_version_id=version.id)
            )


async def test_deleting_agent_removes_versions_even_when_active(session: AsyncSession) -> None:
    agent = Agent(name="Analyst", category="analysis")
    await _add(session, agent)
    version = AgentVersion(agent_id=agent.id, version=1, configuration={"skills": ["analysis"]})
    await _add(session, version)
    agent.active_version_id = version.id
    agent.status = AgentStatus.ACTIVE
    await session.flush()

    await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent.id})

    assert await _count(session, AgentVersion) == 0


# --- workflows, tasks and dependencies ----------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"budget_limit": Decimal("-0.01")},
        {"currency": "usd"},
        {"max_parallel_tasks": 0},
    ],
)
async def test_workflow_check_constraints(session: AsyncSession, fields: dict[str, Any]) -> None:
    workspace = await _workspace(session)

    await _expect_violation(
        session,
        Workflow(
            workspace_id=workspace.id,
            created_by_user_id=workspace.owner_id,
            prompt="p",
            autonomy_policy="approval_required",
            **fields,
        ),
    )


async def test_invalid_statuses_are_rejected_by_the_database(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    workflow = await _workflow(session, workspace)
    task = await _task(session, workflow)

    await _expect_sql_violation(
        session, "UPDATE workflows SET status = 'DONE' WHERE id = :id", id=workflow.id
    )
    await _expect_sql_violation(
        session, "UPDATE workflow_tasks SET status = 'DONE' WHERE id = :id", id=task.id
    )


async def test_task_cannot_point_at_a_workflow_in_another_workspace(
    session: AsyncSession,
) -> None:
    mine, theirs = await _workspace(session), await _workspace(session)
    their_workflow = await _workflow(session, theirs)

    await _expect_violation(
        session,
        WorkflowTask(
            workspace_id=mine.id, workflow_id=their_workflow.id, title="t", description="d"
        ),
    )


async def test_parent_task_must_be_in_the_same_workflow(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    first, second = await _workflow(session, workspace), await _workflow(session, workspace)
    parent = await _task(session, first)

    await _expect_violation(
        session,
        WorkflowTask(
            workspace_id=workspace.id,
            workflow_id=second.id,
            parent_task_id=parent.id,
            title="t",
            description="d",
        ),
    )


async def test_dependency_rules(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    workflow, other_workflow = (
        await _workflow(session, workspace),
        await _workflow(session, workspace),
    )
    a, b = await _task(session, workflow), await _task(session, workflow)
    foreign = await _task(session, other_workflow)

    await _add(
        session, TaskDependency(task_id=b.id, depends_on_task_id=a.id, workflow_id=workflow.id)
    )

    # Duplicate edge, self-dependency, and an edge into another workflow are all rejected.
    await _expect_violation(
        session, TaskDependency(task_id=b.id, depends_on_task_id=a.id, workflow_id=workflow.id)
    )
    await _expect_violation(
        session, TaskDependency(task_id=a.id, depends_on_task_id=a.id, workflow_id=workflow.id)
    )
    await _expect_violation(
        session,
        TaskDependency(task_id=a.id, depends_on_task_id=foreign.id, workflow_id=workflow.id),
    )
    await _expect_violation(
        session,
        TaskDependency(task_id=a.id, depends_on_task_id=foreign.id, workflow_id=other_workflow.id),
    )


# --- events -------------------------------------------------------------------------------


async def test_events_are_ordered_and_append_only(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    workflow = await _workflow(session, workspace)
    first = WorkflowEvent(
        workspace_id=workspace.id,
        workflow_id=workflow.id,
        event_type=WorkflowEventType.WORKFLOW_CREATED,
    )
    second = WorkflowEvent(
        workspace_id=workspace.id,
        workflow_id=workflow.id,
        event_type=WorkflowEventType.PLAN_CREATED,
    )
    await _add(session, first)
    await _add(session, second)
    await session.refresh(first)
    await session.refresh(second)
    assert second.sequence > first.sequence

    await _expect_sql_violation(
        session, "UPDATE workflow_events SET payload = '{}'::jsonb WHERE id = :id", id=first.id
    )
    await session.execute(text("DELETE FROM workflow_events WHERE id = :id"), {"id": first.id})


async def test_event_cannot_reference_another_workflows_task(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    workflow, other = await _workflow(session, workspace), await _workflow(session, workspace)
    foreign_task = await _task(session, other)

    await _expect_violation(
        session,
        WorkflowEvent(
            workspace_id=workspace.id,
            workflow_id=workflow.id,
            task_id=foreign_task.id,
            event_type=WorkflowEventType.TASK_CREATED,
        ),
    )


async def test_event_cannot_claim_another_workspace(session: AsyncSession) -> None:
    mine, theirs = await _workspace(session), await _workspace(session)
    their_workflow = await _workflow(session, theirs)

    await _expect_violation(
        session,
        WorkflowEvent(
            workspace_id=mine.id,
            workflow_id=their_workflow.id,
            event_type=WorkflowEventType.WORKFLOW_CREATED,
        ),
    )


# --- idempotency --------------------------------------------------------------------------


async def test_idempotency_key_constraints(session: AsyncSession) -> None:
    first, second = await _workspace(session), await _workspace(session)
    expires = text("now() + interval '1 hour'")

    def record(workspace: Workspace, **fields: Any) -> IdempotencyKey:
        values: dict[str, Any] = {
            "workspace_id": workspace.id,
            "operation": "workflows.create",
            "idempotency_key": "k1",
            "request_hash": "a" * 64,
            "expires_at": expires,
        }
        values.update(fields)
        return IdempotencyKey(**values)

    await _add(session, record(first), record(second))

    await _expect_violation(session, record(first))  # same workspace, operation and key
    await _expect_violation(session, record(first, idempotency_key="k2", request_hash="short"))
    await _expect_violation(session, record(first, idempotency_key="k3", status="COMPLETED"))
    await _expect_violation(
        session,
        record(first, idempotency_key="k4", status="COMPLETED", response_status_code=700),
    )


# --- deletion -----------------------------------------------------------------------------


async def test_deleting_a_workspace_removes_all_its_data(session: AsyncSession) -> None:
    workspace = await _workspace(session)
    survivor = await _workspace(session)
    workflow = await _workflow(session, workspace)
    a, b = await _task(session, workflow), await _task(session, workflow)
    agent = Agent(workspace_id=workspace.id, name="Custom", category="general")
    await _add(
        session,
        TaskDependency(task_id=b.id, depends_on_task_id=a.id, workflow_id=workflow.id),
        WorkflowEvent(
            workspace_id=workspace.id,
            workflow_id=workflow.id,
            task_id=a.id,
            event_type=WorkflowEventType.TASK_CREATED,
        ),
        WorkspaceMember(
            workspace_id=workspace.id, user_id=workspace.owner_id, role=WorkspaceRole.OWNER
        ),
        agent,
        IdempotencyKey(
            workspace_id=workspace.id,
            operation="workflows.create",
            idempotency_key="k",
            request_hash="b" * 64,
            expires_at=text("now() + interval '1 hour'"),
        ),
    )
    await _workflow(session, survivor)

    await session.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace.id})

    for model in (WorkflowTask, TaskDependency, WorkflowEvent, WorkspaceMember, IdempotencyKey):
        assert await _count(session, model) == 0, model.__tablename__
    assert await _count(session, Agent) == 0
    assert await _count(session, Workflow) == 1  # the other workspace is untouched
    assert await _count(session, User) == 2  # users are not workspace-owned
