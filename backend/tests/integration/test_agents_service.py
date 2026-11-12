"""Registry service against real PostgreSQL: concurrency, runnable versions and seeding."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.agents.seed import load_definitions, seed_builtin_agents
from app.models import (
    Agent,
    AgentProposal,
    AgentProposalStatus,
    AgentVersion,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.schemas.agents import (
    AgentConfiguration,
    AgentCreateRequest,
    AgentUpdateRequest,
    ProposalCreateRequest,
)
from app.services import agents as service
from app.services.authorization import WorkspaceAccess

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

CONFIG = AgentConfiguration.model_validate(
    {
        "system_instruction_ref": "workspace://prompts/x/v1",
        "capabilities": ["analysis"],
        "tools": [{"tool": "document_retrieval"}],
    }
)


async def _workspace_with(engine: AsyncEngine, *roles: WorkspaceRole) -> list[WorkspaceAccess]:
    """A workspace and one user per role; returns their access proofs."""
    async with AsyncSession(engine) as session, session.begin():
        users = [
            User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h") for _ in roles
        ]
        session.add_all(users)
        await session.flush()
        workspace = Workspace(owner_id=users[0].id, name="W")
        session.add(workspace)
        await session.flush()
        session.add_all(
            WorkspaceMember(workspace_id=workspace.id, user_id=u.id, role=r)
            for u, r in zip(users, roles, strict=True)
        )
        return [
            WorkspaceAccess(workspace_id=workspace.id, user_id=u.id, role=r)
            for u, r in zip(users, roles, strict=True)
        ]


async def _run(engine: AsyncEngine, fn: Any, *args: Any, **kwargs: Any) -> Any:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await fn(session, *args, **kwargs)


async def _gather(*coroutines: Any) -> list[Any]:
    return list(await asyncio.gather(*coroutines, return_exceptions=True))


async def _count(engine: AsyncEngine, statement: Any) -> int:
    async with AsyncSession(engine) as session:
        value: int = (await session.execute(statement)).scalar_one()
        return value


async def _new_agent(engine: AsyncEngine, owner: WorkspaceAccess) -> uuid.UUID:
    request = AgentCreateRequest(name="Analyst X", category="analysis", configuration=CONFIG)
    view = await _run(engine, service.create_agent, owner, request)
    agent_id: uuid.UUID = view.agent.id
    return agent_id


async def test_concurrent_edits_with_the_same_revision_cannot_both_win(engine: AsyncEngine) -> None:
    (owner,) = await _workspace_with(engine, WorkspaceRole.OWNER)
    agent_id = await _new_agent(engine, owner)

    def edit(text: str) -> Any:
        request = AgentUpdateRequest(revision=1, description=text)
        return _run(engine, service.update_draft, owner, agent_id, request)

    results = await _gather(*(edit(f"edit {i}") for i in range(5)))

    winners = [r for r in results if isinstance(r, service.AgentView)]
    assert len(winners) == 1
    assert all(
        isinstance(r, service.AgentRevisionConflictError) for r in results if r not in winners
    )
    async with AsyncSession(engine) as session:
        agent = await session.get(Agent, agent_id)
    assert agent is not None and agent.revision == 2
    assert agent.description == winners[0].agent.description


async def test_concurrent_case_variant_names_create_exactly_one_agent(
    engine: AsyncEngine,
) -> None:
    """OD-15: the database index closes the race the application pre-check cannot."""
    (owner,) = await _workspace_with(engine, WorkspaceRole.OWNER)

    def create(name: str) -> Any:
        request = AgentCreateRequest(name=name, category="analysis", configuration=CONFIG)
        return _run(engine, service.create_agent, owner, request)

    results = await _gather(*(create(n) for n in ("Scout", "SCOUT", "scout", "sCoUt")))

    assert sum(isinstance(r, service.AgentView) for r in results) == 1
    assert all(
        isinstance(r, service.AgentNameTakenError)
        for r in results
        if not isinstance(r, service.AgentView)
    )
    agents = select(func.count()).where(func.lower(Agent.name) == "scout")
    assert await _count(engine, agents) == 1


async def test_concurrent_publishes_create_exactly_one_version(engine: AsyncEngine) -> None:
    (owner,) = await _workspace_with(engine, WorkspaceRole.OWNER)
    agent_id = await _new_agent(engine, owner)

    results = await _gather(
        *(_run(engine, service.publish, owner, agent_id, revision=1) for _ in range(4))
    )

    assert sum(isinstance(r, AgentVersion) for r in results) == 1
    versions = select(func.count()).where(AgentVersion.agent_id == agent_id)
    assert await _count(engine, versions) == 1


async def test_concurrent_decisions_on_one_proposal_resolve_once(engine: AsyncEngine) -> None:
    owner, admin, proposer = await _workspace_with(
        engine, WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER
    )
    request = ProposalCreateRequest(
        name="Scanner", category="research", rationale="r", configuration=CONFIG
    )
    proposal = await _run(engine, service.submit_proposal, proposer, request)

    results = await _gather(
        _run(engine, service.approve_proposal, owner, proposal.id, note=""),
        _run(engine, service.approve_proposal, admin, proposal.id, note=""),
        _run(engine, service.reject_proposal, admin, proposal.id, note=""),
    )

    assert sum(isinstance(r, AgentProposal) for r in results) == 1
    assert all(
        isinstance(r, service.ProposalAlreadyDecidedError)
        for r in results
        if not isinstance(r, AgentProposal)
    )
    winner = next(r for r in results if isinstance(r, AgentProposal))
    approved = select(func.count()).where(AgentProposal.status == AgentProposalStatus.APPROVED)
    agents = select(func.count()).where(Agent.workspace_id == owner.workspace_id)
    expected = 1 if winner.status == AgentProposalStatus.APPROVED else 0
    assert await _count(engine, approved) == expected
    assert await _count(engine, agents) == expected


async def test_new_assignments_use_the_current_active_version(engine: AsyncEngine) -> None:
    (owner,) = await _workspace_with(engine, WorkspaceRole.OWNER)
    (stranger,) = await _workspace_with(engine, WorkspaceRole.OWNER)
    agent_id = await _new_agent(engine, owner)

    async def resolve(access: WorkspaceAccess) -> AgentVersion:
        async with AsyncSession(engine) as session:
            return await service.resolve_runnable_version(
                session, workspace_id=access.workspace_id, agent_id=agent_id
            )

    with pytest.raises(service.AgentNotRunnableError):
        await resolve(owner)  # no published version yet
    v1 = await _run(engine, service.publish, owner, agent_id, revision=1)
    with pytest.raises(service.AgentNotRunnableError):
        await resolve(owner)  # published but inactive
    await _run(engine, service.set_active, owner, agent_id, revision=2, active=True)
    assert (await resolve(owner)).id == v1.id

    updated = AgentConfiguration.model_validate(
        {**CONFIG.model_dump(), "capabilities": ["calculation"]}
    )
    await _run(
        engine,
        service.update_draft,
        owner,
        agent_id,
        AgentUpdateRequest(revision=3, configuration=updated),
    )
    v2 = await _run(engine, service.publish, owner, agent_id, revision=4)

    assert (await resolve(owner)).id == v2.id
    with pytest.raises(service.AgentNotFoundError):
        await resolve(stranger)  # another workspace cannot run it


async def test_seeding_is_idempotent_and_versions_changed_definitions(engine: AsyncEngine) -> None:
    first = await _run(engine, seed_builtin_agents)
    second = await _run(engine, seed_builtin_agents)

    assert len(first.created) == 6 and not first.updated
    assert len(second.unchanged) == 6 and not second.created and not second.updated

    definitions = load_definitions()
    writer = next(d for d in definitions if d.name == "Writer")
    changed = writer.model_copy(
        update={
            "configuration": writer.configuration.model_copy(
                update={"capabilities": ["report_writing", "documentation"]}
            )
        }
    )
    third = await _run(
        engine, seed_builtin_agents, [changed if d.name == "Writer" else d for d in definitions]
    )

    assert third.updated == ["Writer"]
    async with AsyncSession(engine) as session:
        versions = (
            await session.execute(
                select(AgentVersion.version, AgentVersion.configuration)
                .join(Agent, Agent.id == AgentVersion.agent_id)
                .where(Agent.name == "Writer", Agent.workspace_id.is_(None))
                .order_by(AgentVersion.version)
            )
        ).all()
    assert [v[0] for v in versions] == [1, 2]
    assert versions[0][1]["capabilities"] == ["report_writing", "documentation", "editing"]
    assert versions[1][1]["capabilities"] == ["report_writing", "documentation"]
