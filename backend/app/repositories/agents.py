"""Agent registry queries. Visibility: a workspace sees its own agents and built-ins (NULL
workspace); it never sees another workspace's agents or proposals."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentProposal, AgentProposalStatus, AgentStatus, AgentVersion


def _visible_to(workspace_id: uuid.UUID) -> Any:
    return or_(Agent.workspace_id == workspace_id, Agent.workspace_id.is_(None))


async def list_visible(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    status: AgentStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Agent, AgentVersion | None]], int]:
    filters = [_visible_to(workspace_id)]
    if status is not None:
        filters.append(Agent.status == status)
    rows = await session.execute(
        select(Agent, AgentVersion)
        .outerjoin(AgentVersion, AgentVersion.id == Agent.active_version_id)
        .where(*filters)
        # Built-ins first, then by name.
        .order_by(Agent.workspace_id.is_not(None), Agent.name, Agent.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(Agent).where(*filters))
    return [(row[0], row[1]) for row in rows], total.scalar_one()


async def get_visible(
    session: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
) -> tuple[Agent, AgentVersion | None] | None:
    row = (
        await session.execute(
            select(Agent, AgentVersion)
            .outerjoin(AgentVersion, AgentVersion.id == Agent.active_version_id)
            .where(Agent.id == agent_id, _visible_to(workspace_id))
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def lock_visible(
    session: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
) -> Agent | None:
    statement = (
        select(Agent)
        .where(Agent.id == agent_id, _visible_to(workspace_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def get_version(session: AsyncSession, version_id: uuid.UUID | None) -> AgentVersion | None:
    if version_id is None:
        return None
    return await session.get(AgentVersion, version_id)


async def latest_version_number(session: AsyncSession, agent_id: uuid.UUID) -> int:
    statement = select(func.coalesce(func.max(AgentVersion.version), 0)).where(
        AgentVersion.agent_id == agent_id
    )
    return int((await session.execute(statement)).scalar_one())


async def list_versions(
    session: AsyncSession, *, agent_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[AgentVersion], int]:
    scope = AgentVersion.agent_id == agent_id
    rows = await session.execute(
        select(AgentVersion)
        .where(scope)
        .order_by(AgentVersion.version.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(AgentVersion).where(scope))
    return list(rows.scalars()), total.scalar_one()


async def name_taken(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    name: str,
    exclude_agent_id: uuid.UUID | None = None,
) -> bool:
    """Case-insensitive; `exclude_agent_id` lets an agent be renamed to a case variant."""
    owner = (
        Agent.workspace_id.is_(None) if workspace_id is None else Agent.workspace_id == workspace_id
    )
    statement = select(Agent.id).where(owner, func.lower(Agent.name) == name.lower())
    if exclude_agent_id is not None:
        statement = statement.where(Agent.id != exclude_agent_id)
    return (await session.execute(statement)).first() is not None


async def find_builtin(session: AsyncSession, name: str) -> Agent | None:
    statement = (
        select(Agent)
        .where(Agent.workspace_id.is_(None), Agent.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def list_proposals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    status: AgentProposalStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[AgentProposal], int]:
    filters: list[Any] = [AgentProposal.workspace_id == workspace_id]
    if status is not None:
        filters.append(AgentProposal.status == status)
    rows = await session.execute(
        select(AgentProposal)
        .where(*filters)
        .order_by(AgentProposal.created_at.desc(), AgentProposal.id)
        .limit(limit)
        .offset(offset)
    )
    total = await session.execute(select(func.count()).select_from(AgentProposal).where(*filters))
    return list(rows.scalars()), total.scalar_one()


async def lock_proposal(
    session: AsyncSession, *, workspace_id: uuid.UUID, proposal_id: uuid.UUID
) -> AgentProposal | None:
    statement = (
        select(AgentProposal)
        .where(AgentProposal.id == proposal_id, AgentProposal.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(statement)).scalar_one_or_none()
