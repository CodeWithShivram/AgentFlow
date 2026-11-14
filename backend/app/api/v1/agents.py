"""Agent registry endpoints, all scoped to /workspaces/{workspace_id} (ADR-0008).

Routes check the minimum role and translate HTTP; rules live in app.services.agents.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.agents.catalog import CAPABILITIES, CATEGORIES, TOOLS
from app.api.deps import CurrentPrincipal, DbSession, workspace_role
from app.models import AgentProposal, AgentProposalStatus, AgentStatus, AgentVersion, WorkspaceRole
from app.schemas.agents import (
    AgentCatalogResponse,
    AgentCreateRequest,
    AgentResponse,
    AgentUpdateRequest,
    AgentVersionResponse,
    CatalogTool,
    ProposalCreateRequest,
    ProposalDecisionRequest,
    ProposalResponse,
    RevisionRequest,
)
from app.schemas.workspaces import Page
from app.services import agents as service
from app.services.authorization import WorkspaceAccess
from app.tools.registry import REGISTRY

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["agents"])
catalog_router = APIRouter(tags=["agents"])


@catalog_router.get("/agent-catalog")
async def agent_catalog(_: CurrentPrincipal) -> AgentCatalogResponse:
    """The capabilities, categories and tools an agent configuration may use (platform policy,
    the same for every workspace). Read-only."""
    return AgentCatalogResponse(
        capabilities=sorted(CAPABILITIES),
        categories=sorted(CATEGORIES),
        tools=[
            CatalogTool(
                name=t.name,
                risk=t.risk.value,
                description=t.description,
                available_in_phase_1=t.available_in_phase_1,
                usable_now=REGISTRY[t.name].available,
            )
            for t in sorted(TOOLS.values(), key=lambda t: (t.risk, t.name))
        ],
    )


Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Viewer = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.VIEWER))]
Member = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.MEMBER))]
Admin = Annotated[WorkspaceAccess, Depends(workspace_role(WorkspaceRole.ADMIN))]


def _agent(view: service.AgentView) -> AgentResponse:
    agent, version = view.agent, view.active_version
    return AgentResponse(
        id=agent.id,
        workspace_id=agent.workspace_id,
        is_builtin=agent.workspace_id is None,
        name=agent.name,
        category=agent.category,
        description=agent.description,
        status=agent.status,
        revision=agent.revision,
        active_version=version.version if version else None,
        draft_configuration=agent.draft_configuration,
        has_unpublished_changes=version is None
        or version.configuration != agent.draft_configuration,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _version(version: AgentVersion) -> AgentVersionResponse:
    return AgentVersionResponse(
        id=version.id,
        agent_id=version.agent_id,
        version=version.version,
        configuration=version.configuration,
        created_at=version.created_at,
        created_by=version.created_by,
    )


def _proposal(proposal: AgentProposal) -> ProposalResponse:
    return ProposalResponse(
        id=proposal.id,
        workspace_id=proposal.workspace_id,
        name=proposal.name,
        category=proposal.category,
        description=proposal.description,
        rationale=proposal.rationale,
        proposed_configuration=proposal.proposed_configuration,
        status=proposal.status,
        requested_by=proposal.requested_by,
        reviewed_by=proposal.reviewed_by,
        reviewed_at=proposal.reviewed_at,
        review_note=proposal.review_note,
        agent_id=proposal.agent_id,
        source_workflow_id=proposal.source_workflow_id,
        source_task_id=proposal.source_task_id,
        created_at=proposal.created_at,
    )


# --- agents ----------------------------------------------------------------------------------


@router.get("/agents")
async def list_agents(
    access: Viewer,
    session: DbSession,
    status_filter: Annotated[AgentStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AgentResponse]:
    """The workspace's agents plus the built-in specialists."""
    views, total = await service.list_agents(
        session, access, status=status_filter, limit=limit, offset=offset
    )
    return Page(items=[_agent(v) for v in views], total=total, limit=limit, offset=offset)


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreateRequest, access: Admin, session: DbSession
) -> AgentResponse:
    """Creates an INACTIVE agent with a draft configuration (no published version yet)."""
    return _agent(await service.create_agent(session, access, body))


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: uuid.UUID, access: Viewer, session: DbSession) -> AgentResponse:
    return _agent(await service.get_agent(session, access, agent_id))


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdateRequest, access: Admin, session: DbSession
) -> AgentResponse:
    """Edits name/category/description and the draft. Requires the current `revision`."""
    return _agent(await service.update_draft(session, access, agent_id, body))


@router.post("/agents/{agent_id}/publish", status_code=status.HTTP_201_CREATED)
async def publish_agent(
    agent_id: uuid.UUID, body: RevisionRequest, access: Admin, session: DbSession
) -> AgentVersionResponse:
    """Snapshots the draft as the next immutable version and makes it active."""
    return _version(await service.publish(session, access, agent_id, revision=body.revision))


@router.post("/agents/{agent_id}/activate")
async def activate_agent(
    agent_id: uuid.UUID, body: RevisionRequest, access: Admin, session: DbSession
) -> AgentResponse:
    view = await service.set_active(session, access, agent_id, revision=body.revision, active=True)
    return _agent(view)


@router.post("/agents/{agent_id}/deactivate")
async def deactivate_agent(
    agent_id: uuid.UUID, body: RevisionRequest, access: Admin, session: DbSession
) -> AgentResponse:
    view = await service.set_active(session, access, agent_id, revision=body.revision, active=False)
    return _agent(view)


@router.get("/agents/{agent_id}/versions")
async def list_agent_versions(
    agent_id: uuid.UUID,
    access: Viewer,
    session: DbSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AgentVersionResponse]:
    """Published versions, newest first."""
    versions, total = await service.list_versions(
        session, access, agent_id, limit=limit, offset=offset
    )
    return Page(items=[_version(v) for v in versions], total=total, limit=limit, offset=offset)


# --- proposals -------------------------------------------------------------------------------


@router.post("/agent-proposals", status_code=status.HTTP_201_CREATED)
async def submit_proposal(
    body: ProposalCreateRequest, access: Member, session: DbSession
) -> ProposalResponse:
    return _proposal(await service.submit_proposal(session, access, body))


@router.get("/agent-proposals")
async def list_proposals(
    access: Viewer,
    session: DbSession,
    status_filter: Annotated[AgentProposalStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[ProposalResponse]:
    proposals, total = await service.list_proposals(
        session, access, status=status_filter, limit=limit, offset=offset
    )
    return Page(items=[_proposal(p) for p in proposals], total=total, limit=limit, offset=offset)


@router.post("/agent-proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: uuid.UUID, body: ProposalDecisionRequest, access: Admin, session: DbSession
) -> ProposalResponse:
    """Registers the agent with version 1, INACTIVE. Activation is a separate action."""
    return _proposal(await service.approve_proposal(session, access, proposal_id, note=body.note))


@router.post("/agent-proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: uuid.UUID, body: ProposalDecisionRequest, access: Admin, session: DbSession
) -> ProposalResponse:
    return _proposal(await service.reject_proposal(session, access, proposal_id, note=body.note))
