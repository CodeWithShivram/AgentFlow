"""Agent registry: drafts, immutable versions, activation, proposals and audit (ADR-0008).

Registry operations only store and validate configuration. They never execute instructions or
tools. Each public mutation owns its transaction, writes an audit event in it, and commits.

Who may do what (roles via WorkspaceAccess; routes enforce the minimum role):
- VIEWER+: list and read agents, versions and proposals.
- MEMBER+: submit proposals.
- ADMIN+: create agents, edit drafts, publish, activate/deactivate, approve/reject proposals.
- OWNER only: any of the above that makes a HIGH-risk (code-executing) tool effective, i.e.
  publishing, approving or activating a configuration that grants one.
- Built-in agents are read-only to every workspace.
- A proposal can never be decided by the user who submitted it.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalog import ToolRisk, highest_tool_risk
from app.core.errors import AppError
from app.models import (
    Agent,
    AgentAuditAction,
    AgentAuditEvent,
    AgentProposal,
    AgentProposalStatus,
    AgentStatus,
    AgentVersion,
    WorkspaceRole,
)
from app.repositories import agents as repository
from app.schemas.agents import (
    AgentConfiguration,
    AgentCreateRequest,
    AgentUpdateRequest,
    ProposalCreateRequest,
)
from app.services.authorization import WorkspaceAccess

logger = logging.getLogger("agentflow.agents")


class AgentNotFoundError(AppError):
    status_code = 404
    code = "AGENT_NOT_FOUND"
    message = "Agent not found."


class AgentReadOnlyError(AppError):
    status_code = 403
    code = "AGENT_READ_ONLY"
    message = "Built-in agents are managed by the platform and cannot be changed."


class AgentRevisionConflictError(AppError):
    status_code = 409
    code = "AGENT_REVISION_CONFLICT"
    message = "The agent was changed by someone else. Reload it and try again."


class AgentNameTakenError(AppError):
    status_code = 409
    code = "AGENT_NAME_TAKEN"
    message = "An agent with this name already exists in the workspace."


class NothingToPublishError(AppError):
    status_code = 409
    code = "NOTHING_TO_PUBLISH"
    message = "The draft is identical to the active version."


class AgentNotPublishedError(AppError):
    status_code = 409
    code = "AGENT_NOT_PUBLISHED"
    message = "Publish a version before activating the agent."


class AgentNotRunnableError(AppError):
    status_code = 409
    code = "AGENT_NOT_RUNNABLE"
    message = "The agent is not active or has no published version."


class InvalidAgentConfigurationError(AppError):
    status_code = 422
    code = "AGENT_CONFIGURATION_INVALID"
    message = "The agent configuration is not valid."


class ToolRequiresOwnerError(AppError):
    status_code = 403
    code = "TOOL_REQUIRES_OWNER"
    message = "Only a workspace owner may grant tools that execute code."


class ProposalNotFoundError(AppError):
    status_code = 404
    code = "PROPOSAL_NOT_FOUND"
    message = "Proposal not found."


class ProposalAlreadyDecidedError(AppError):
    status_code = 409
    code = "PROPOSAL_ALREADY_DECIDED"
    message = "This proposal has already been approved or rejected."


class SelfReviewError(AppError):
    status_code = 403
    code = "PROPOSAL_SELF_REVIEW"
    message = "A proposal must be reviewed by someone other than its author."


@dataclass(frozen=True)
class AgentView:
    agent: Agent
    active_version: AgentVersion | None


# --- validation and policy -------------------------------------------------------------------


def validate_configuration(raw: Any) -> AgentConfiguration:
    """Re-validates stored configuration against the current schema and catalog."""
    try:
        return AgentConfiguration.model_validate(raw)
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
        raise InvalidAgentConfigurationError(details={"errors": errors}) from exc


def _require_tool_authority(access: WorkspaceAccess, configuration: AgentConfiguration) -> None:
    if (
        highest_tool_risk(configuration.tool_names()) == ToolRisk.HIGH
        and access.role != WorkspaceRole.OWNER
    ):
        raise ToolRequiresOwnerError()


def _stored(configuration: AgentConfiguration) -> dict[str, Any]:
    return configuration.model_dump(mode="json")


def _audit(
    session: AsyncSession,
    action: AgentAuditAction,
    *,
    workspace_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    agent_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AgentAuditEvent(
            workspace_id=workspace_id,
            agent_id=agent_id,
            proposal_id=proposal_id,
            action=action,
            actor_user_id=actor_user_id,
            details=details or {},
        )
    )


async def _lock_owned(
    session: AsyncSession, access: WorkspaceAccess, agent_id: uuid.UUID, revision: int
) -> Agent:
    # Built-ins are readable but never lockable: row-level security only lets FOR UPDATE reach
    # the workspace's own rows (ADR-0012). Check before locking so they report read-only.
    visible = await repository.get_visible(
        session, workspace_id=access.workspace_id, agent_id=agent_id
    )
    if visible is None:
        raise AgentNotFoundError()
    if visible[0].workspace_id is None:
        raise AgentReadOnlyError()
    agent = await repository.lock_visible(
        session, workspace_id=access.workspace_id, agent_id=agent_id
    )
    if agent is None:
        raise AgentNotFoundError()
    if agent.revision != revision:
        raise AgentRevisionConflictError(details={"current_revision": agent.revision})
    return agent


async def _write_or_name_conflict(session: AsyncSession, *, commit: bool) -> None:
    """Flushes or commits; a concurrent duplicate agent name becomes AgentNameTakenError."""
    try:
        await (session.commit() if commit else session.flush())
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_agents_workspace_id_lower_name":
            raise AgentNameTakenError() from exc
        raise


# --- reads -----------------------------------------------------------------------------------


async def list_agents(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    status: AgentStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[AgentView], int]:
    rows, total = await repository.list_visible(
        session, workspace_id=access.workspace_id, status=status, limit=limit, offset=offset
    )
    return [AgentView(agent, version) for agent, version in rows], total


async def get_agent(
    session: AsyncSession, access: WorkspaceAccess, agent_id: uuid.UUID
) -> AgentView:
    found = await repository.get_visible(
        session, workspace_id=access.workspace_id, agent_id=agent_id
    )
    if found is None:
        raise AgentNotFoundError()
    return AgentView(*found)


async def list_versions(
    session: AsyncSession, access: WorkspaceAccess, agent_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[AgentVersion], int]:
    await get_agent(session, access, agent_id)
    return await repository.list_versions(session, agent_id=agent_id, limit=limit, offset=offset)


async def resolve_runnable_version(
    session: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
) -> AgentVersion:
    """The exact version a new task assignment must pin (used by Step 1.7 scheduling)."""
    found = await repository.get_visible(session, workspace_id=workspace_id, agent_id=agent_id)
    if found is None:
        raise AgentNotFoundError()
    agent, version = found
    if agent.status != AgentStatus.ACTIVE or version is None:
        raise AgentNotRunnableError()
    return version


# --- agent lifecycle -------------------------------------------------------------------------


async def create_agent(
    session: AsyncSession, access: WorkspaceAccess, request: AgentCreateRequest
) -> AgentView:
    """Creates an INACTIVE agent holding only a draft. Nothing is executable until published
    and activated."""
    if await repository.name_taken(session, workspace_id=access.workspace_id, name=request.name):
        raise AgentNameTakenError()
    agent = Agent(
        id=uuid.uuid4(),
        workspace_id=access.workspace_id,
        name=request.name,
        category=request.category,
        description=request.description,
        status=AgentStatus.INACTIVE,
        draft_configuration=_stored(request.configuration),
        revision=1,
    )
    session.add(agent)
    # Flush first: without ORM relationships the unit of work cannot order the audit insert.
    await _write_or_name_conflict(session, commit=False)
    _audit(
        session,
        AgentAuditAction.CREATED,
        workspace_id=access.workspace_id,
        actor_user_id=access.user_id,
        agent_id=agent.id,
    )
    await _write_or_name_conflict(session, commit=True)
    logger.info("agent created", extra={"agent_id": str(agent.id)})
    return AgentView(agent, None)


async def update_draft(
    session: AsyncSession, access: WorkspaceAccess, agent_id: uuid.UUID, request: AgentUpdateRequest
) -> AgentView:
    agent = await _lock_owned(session, access, agent_id, request.revision)
    changed: list[str] = []
    if request.name is not None and request.name != agent.name:
        if await repository.name_taken(
            session, workspace_id=access.workspace_id, name=request.name, exclude_agent_id=agent.id
        ):
            raise AgentNameTakenError()
        agent.name = request.name
        changed.append("name")
    if request.category is not None and request.category != agent.category:
        agent.category = request.category
        changed.append("category")
    if request.description is not None and request.description != agent.description:
        agent.description = request.description
        changed.append("description")
    if request.configuration is not None:
        stored = _stored(request.configuration)
        if stored != agent.draft_configuration:
            agent.draft_configuration = stored
            changed.append("configuration")
    if changed:
        agent.revision += 1
        _audit(
            session,
            AgentAuditAction.DRAFT_UPDATED,
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            agent_id=agent.id,
            details={"fields": changed, "revision": agent.revision},
        )
    await _write_or_name_conflict(session, commit=True)
    return AgentView(agent, await repository.get_version(session, agent.active_version_id))


async def publish(
    session: AsyncSession, access: WorkspaceAccess, agent_id: uuid.UUID, *, revision: int
) -> AgentVersion:
    """Snapshots the draft into a new immutable version and makes it the active version."""
    agent = await _lock_owned(session, access, agent_id, revision)
    configuration = validate_configuration(agent.draft_configuration)
    _require_tool_authority(access, configuration)
    current = await repository.get_version(session, agent.active_version_id)
    stored = _stored(configuration)
    if current is not None and current.configuration == stored:
        raise NothingToPublishError()
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version=await repository.latest_version_number(session, agent.id) + 1,
        configuration=stored,
        created_by=access.user_id,
    )
    session.add(version)
    await session.flush()
    agent.active_version_id = version.id
    agent.draft_configuration = stored
    agent.revision += 1
    _audit(
        session,
        AgentAuditAction.VERSION_PUBLISHED,
        workspace_id=access.workspace_id,
        actor_user_id=access.user_id,
        agent_id=agent.id,
        details={"version": version.version, "revision": agent.revision},
    )
    await session.commit()
    logger.info(
        "agent version published", extra={"agent_id": str(agent.id), "version": version.version}
    )
    return version


async def set_active(
    session: AsyncSession,
    access: WorkspaceAccess,
    agent_id: uuid.UUID,
    *,
    revision: int,
    active: bool,
) -> AgentView:
    agent = await _lock_owned(session, access, agent_id, revision)
    version = await repository.get_version(session, agent.active_version_id)
    target = AgentStatus.ACTIVE if active else AgentStatus.INACTIVE
    if active:
        if version is None:
            raise AgentNotPublishedError()
        _require_tool_authority(access, validate_configuration(version.configuration))
    if agent.status != target:
        agent.status = target
        agent.revision += 1
        _audit(
            session,
            AgentAuditAction.ACTIVATED if active else AgentAuditAction.DEACTIVATED,
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            agent_id=agent.id,
            details={"version": version.version if version else None, "revision": agent.revision},
        )
    await session.commit()
    return AgentView(agent, version)


# --- dynamic proposals -----------------------------------------------------------------------


async def submit_proposal(
    session: AsyncSession, access: WorkspaceAccess, request: ProposalCreateRequest
) -> AgentProposal:
    """Stores the proposal separately from agents. It grants nothing until approved."""
    proposal = AgentProposal(
        id=uuid.uuid4(),
        workspace_id=access.workspace_id,
        name=request.name,
        category=request.category,
        description=request.description,
        rationale=request.rationale,
        proposed_configuration=_stored(request.configuration),
        status=AgentProposalStatus.PENDING,
        requested_by=access.user_id,
    )
    session.add(proposal)
    await session.flush()  # the audit row references it
    _audit(
        session,
        AgentAuditAction.PROPOSAL_SUBMITTED,
        workspace_id=access.workspace_id,
        actor_user_id=access.user_id,
        proposal_id=proposal.id,
    )
    await session.commit()
    return proposal


async def list_proposals(
    session: AsyncSession,
    access: WorkspaceAccess,
    *,
    status: AgentProposalStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[AgentProposal], int]:
    return await repository.list_proposals(
        session, workspace_id=access.workspace_id, status=status, limit=limit, offset=offset
    )


async def _lock_pending(
    session: AsyncSession, access: WorkspaceAccess, proposal_id: uuid.UUID
) -> AgentProposal:
    proposal = await repository.lock_proposal(
        session, workspace_id=access.workspace_id, proposal_id=proposal_id
    )
    if proposal is None:
        raise ProposalNotFoundError()
    if proposal.status != AgentProposalStatus.PENDING:
        raise ProposalAlreadyDecidedError()
    if proposal.requested_by == access.user_id:
        raise SelfReviewError()
    return proposal


async def approve_proposal(
    session: AsyncSession, access: WorkspaceAccess, proposal_id: uuid.UUID, *, note: str
) -> AgentProposal:
    """Registers the proposed agent with an immutable version 1, still INACTIVE.

    The approved version is exactly the frozen proposed configuration, re-validated against
    the current catalog and the reviewer's authority. Activation is a separate decision.
    """
    proposal = await _lock_pending(session, access, proposal_id)
    configuration = validate_configuration(proposal.proposed_configuration)
    _require_tool_authority(access, configuration)
    if await repository.name_taken(session, workspace_id=access.workspace_id, name=proposal.name):
        raise AgentNameTakenError()
    agent = Agent(
        id=uuid.uuid4(),
        workspace_id=access.workspace_id,
        name=proposal.name,
        category=proposal.category,
        description=proposal.description,
        status=AgentStatus.INACTIVE,
        draft_configuration=_stored(configuration),
        revision=1,
    )
    session.add(agent)
    await session.flush()
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version=1,
        configuration=_stored(configuration),
        created_by=access.user_id,
    )
    session.add(version)
    await session.flush()
    agent.active_version_id = version.id
    proposal.status = AgentProposalStatus.APPROVED
    proposal.reviewed_by = access.user_id
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_note = note or None
    proposal.agent_id = agent.id
    _audit(
        session,
        AgentAuditAction.PROPOSAL_APPROVED,
        workspace_id=access.workspace_id,
        actor_user_id=access.user_id,
        agent_id=agent.id,
        proposal_id=proposal.id,
        details={"version": 1},
    )
    await _write_or_name_conflict(session, commit=True)
    logger.info("agent proposal approved", extra={"proposal_id": str(proposal.id)})
    return proposal


async def reject_proposal(
    session: AsyncSession, access: WorkspaceAccess, proposal_id: uuid.UUID, *, note: str
) -> AgentProposal:
    proposal = await _lock_pending(session, access, proposal_id)
    proposal.status = AgentProposalStatus.REJECTED
    proposal.reviewed_by = access.user_id
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_note = note or None
    _audit(
        session,
        AgentAuditAction.PROPOSAL_REJECTED,
        workspace_id=access.workspace_id,
        actor_user_id=access.user_id,
        proposal_id=proposal.id,
    )
    await session.commit()
    return proposal


# --- platform proposals (ADR-0024) -----------------------------------------------------------

# The safe template: the General runtime's instructions, the task's skills, and no tools.
PROPOSAL_INSTRUCTION_REF = "builtin://general/v1"


@dataclass(frozen=True)
class Gap:
    """A task no active agent can take, by its required skills (ADR-0023 hard filters)."""

    task_id: uuid.UUID
    task_title: str
    skills: list[str]


def _skill_label(skill: str) -> str:
    return skill.replace("_", " ")


async def propose_specialists(
    session: AsyncSession, *, workflow_id: uuid.UUID, workspace_id: uuid.UUID, gaps: list[Gap]
) -> list[AgentProposal]:
    """Files one PENDING proposal per distinct skill set that no active agent covers, in the
    caller's transaction. Nothing is granted: approval still needs an admin who is not the
    proposer, and an approved agent starts INACTIVE (ADR-0008).

    Skipped: skill sets already pending, and sets whose generated name is already an agent's
    (that specialist exists and may only need activating)."""
    pending = await repository.pending_proposal_skill_sets(session, workspace_id=workspace_id)
    created: list[AgentProposal] = []
    for gap in gaps:
        skills = sorted(set(gap.skills))
        if not skills or frozenset(skills) in pending:
            continue
        labels = ", ".join(_skill_label(s) for s in skills)
        name = f"Specialist: {labels}"[:100]
        if await repository.name_taken(session, workspace_id=workspace_id, name=name):
            continue
        configuration = AgentConfiguration(
            system_instruction_ref=PROPOSAL_INSTRUCTION_REF, capabilities=skills, tools=[]
        )
        proposal = AgentProposal(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=name,
            category="custom",
            description=f"Handles tasks that need {labels}.",
            rationale=(
                f"Proposed by the platform: the task '{gap.task_title[:200]}' needs {labels}, "
                "and no active agent has all of these skills. The proposal requests no tools "
                "and uses the General agent's instructions."
            ),
            proposed_configuration=_stored(configuration),
            status=AgentProposalStatus.PENDING,
            requested_by=None,
            source_workflow_id=workflow_id,
            source_task_id=gap.task_id,
        )
        session.add(proposal)
        await session.flush()  # the audit row references it
        _audit(
            session,
            AgentAuditAction.PROPOSAL_SUBMITTED,
            workspace_id=workspace_id,
            actor_user_id=None,
            proposal_id=proposal.id,
            details={"source": "platform", "workflow_id": str(workflow_id)},
        )
        pending.add(frozenset(skills))
        created.append(proposal)
    return created
