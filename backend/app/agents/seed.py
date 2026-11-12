"""Seeds the built-in specialist agents from builtin_agents.json.

Idempotent: a missing built-in is created (version 1, ACTIVE); a built-in whose definition
changed gets a new immutable version; an unchanged one is left alone. Run after migrations:

    uv run python -m app.agents.seed
"""

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import create_engine
from app.models import Agent, AgentAuditAction, AgentAuditEvent, AgentStatus, AgentVersion
from app.repositories import agents as repository
from app.schemas.agents import AgentConfiguration, AgentName, Category, Description

BUILTIN_DEFINITIONS = Path(__file__).with_name("builtin_agents.json")


class BuiltinAgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    category: Category
    description: Description
    configuration: AgentConfiguration


class BuiltinAgentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[BuiltinAgentDefinition]


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def load_definitions(path: Path = BUILTIN_DEFINITIONS) -> list[BuiltinAgentDefinition]:
    return BuiltinAgentFile.model_validate(json.loads(path.read_text(encoding="utf-8"))).agents


def _audit(
    session: AsyncSession, agent_id: uuid.UUID, action: AgentAuditAction, version: int
) -> None:
    session.add(
        AgentAuditEvent(
            workspace_id=None,
            agent_id=agent_id,
            action=action,
            actor_user_id=None,
            details={"version": version, "source": "builtin_seed"},
        )
    )


async def seed_builtin_agents(
    session: AsyncSession, definitions: list[BuiltinAgentDefinition] | None = None
) -> SeedReport:
    report = SeedReport()
    for definition in definitions if definitions is not None else load_definitions():
        stored = definition.configuration.model_dump(mode="json")
        agent = await repository.find_builtin(session, definition.name)
        if agent is None:
            agent = Agent(
                id=uuid.uuid4(),
                workspace_id=None,
                name=definition.name,
                category=definition.category,
                description=definition.description,
                status=AgentStatus.INACTIVE,
                draft_configuration=stored,
                revision=1,
            )
            session.add(agent)
            await session.flush()
            version = AgentVersion(agent_id=agent.id, version=1, configuration=stored)
            session.add(version)
            await session.flush()
            agent.active_version_id = version.id
            agent.status = AgentStatus.ACTIVE
            _audit(session, agent.id, AgentAuditAction.CREATED, 1)
            _audit(session, agent.id, AgentAuditAction.VERSION_PUBLISHED, 1)
            _audit(session, agent.id, AgentAuditAction.ACTIVATED, 1)
            report.created.append(definition.name)
            continue
        current = await repository.get_version(session, agent.active_version_id)
        metadata_changed = (agent.category, agent.description) != (
            definition.category,
            definition.description,
        )
        if current is not None and current.configuration == stored and not metadata_changed:
            report.unchanged.append(definition.name)
            continue
        agent.category = definition.category
        agent.description = definition.description
        if current is None or current.configuration != stored:
            number = await repository.latest_version_number(session, agent.id) + 1
            version = AgentVersion(agent_id=agent.id, version=number, configuration=stored)
            session.add(version)
            await session.flush()
            agent.active_version_id = version.id
            agent.draft_configuration = stored
            _audit(session, agent.id, AgentAuditAction.VERSION_PUBLISHED, number)
        agent.revision += 1
        report.updated.append(definition.name)
    await session.commit()
    return report


async def _main() -> None:
    # Built-ins belong to no workspace; only the administrator may write them (ADR-0012).
    settings = get_settings()
    engine = create_engine(settings, settings.database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            report = await seed_builtin_agents(session)
    finally:
        await engine.dispose()
    # Command-line output for the operator, not application logging.
    print(
        f"built-in agents: created={report.created} updated={report.updated} "
        f"unchanged={report.unchanged}"
    )


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main(), loop_factory=loop_factory)
