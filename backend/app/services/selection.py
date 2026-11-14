"""Agent selection service (ADR-0023): ranks the candidates for a task and stores the rationale of
an assignment. The rules are pure (`app/agents/selection.py`); this module gathers their inputs
from the database, always scoped to the caller's workspace."""

import uuid
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.selection import RULES_VERSION, Candidate, CandidateInput, rank_candidates
from app.models import AgentSelection, Workflow, WorkflowTask
from app.repositories import agents as agents_repository
from app.repositories import workflows as repository
from app.schemas.agents import AgentConfiguration


async def rank_for_task(
    session: AsyncSession, *, workspace_id: uuid.UUID, task: WorkflowTask
) -> list[Candidate]:
    """Every ACTIVE agent visible to the workspace, ranked for this task."""
    busy = await repository.in_flight_by_agent(session, workspace_id=workspace_id)
    candidates = [
        CandidateInput(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_version_id=version.id,
            agent_version=version.version,
            # Stored versions were validated on publish (ADR-0008).
            configuration=AgentConfiguration.model_validate(version.configuration),
            in_flight=busy.get(agent.id, 0),
        )
        for agent, version in await agents_repository.active_with_versions(
            session, workspace_id=workspace_id
        )
    ]
    return rank_candidates(list(task.required_skills), candidates)


def record_selection(
    session: AsyncSession,
    workflow: Workflow,
    task: WorkflowTask,
    ranked: list[Candidate],
    *,
    agent_id: uuid.UUID,
    agent_version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AgentSelection:
    """Adds the rationale of an assignment, in the caller's transaction. The chosen agent's
    candidate is recomputed by the server, never taken from the client."""
    chosen = next(c for c in ranked if c.agent_id == agent_id)
    top = next((c for c in ranked if c.rank == 1), None)
    selection = AgentSelection(
        id=uuid.uuid4(),
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        task_id=task.id,
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        decided_by_user_id=user_id,
        rules_version=RULES_VERSION,
        eligible=chosen.eligible,
        score=Decimal(str(chosen.score)),
        rank=chosen.rank,
        top_agent_id=top.agent_id if top else None,
        candidate_count=len(ranked),
        factors=[asdict(factor) for factor in chosen.factors],
    )
    session.add(selection)
    return selection
