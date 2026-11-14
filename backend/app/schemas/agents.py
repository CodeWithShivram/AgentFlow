"""Agent configuration (strict, versioned) and agent registry API models."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.agents.catalog import CAPABILITIES, CATEGORIES, TOOLS
from app.models import AgentProposalStatus, AgentStatus

_STRICT = ConfigDict(extra="forbid", frozen=True)
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]

# Values that look like provider credentials. Configurations hold references, never keys.
_CREDENTIAL_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-[A-Za-z0-9_-]{16,}",
        r"sk-ant-[A-Za-z0-9_-]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{30,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"Bearer\s+[A-Za-z0-9._-]{20,}",
    )
]


def _no_credentials(value: str) -> str:
    if any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS):
        # The message deliberately does not echo the value.
        raise ValueError("looks like a credential; store a secret reference instead")
    return value


SafeText = Annotated[str, AfterValidator(_no_credentials)]


def _capability(value: str) -> str:
    if value not in CAPABILITIES:
        raise ValueError("unknown capability")
    return value


def _tool(value: str) -> str:
    if value not in TOOLS:
        raise ValueError("unknown tool")
    return value


def _category(value: str) -> str:
    if value not in CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    return value


Capability = Annotated[Slug, AfterValidator(_capability)]
ToolName = Annotated[Slug, AfterValidator(_tool)]
Category = Annotated[Slug, AfterValidator(_category)]
AgentName = Annotated[
    SafeText, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)
]
Description = Annotated[SafeText, StringConstraints(max_length=2000)]


class ModelSettings(BaseModel):
    """A reference to a model profile resolved by the model gateway (Phase 2) from environment
    configuration. Provider credentials never live here."""

    model_config = _STRICT

    profile: Slug = "default"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=200_000)
    # Optional pointer to a secret held by the platform's secret store, e.g. "secret://openai".
    credential_ref: (
        Annotated[str, StringConstraints(pattern=r"^secret://[a-z0-9][a-z0-9_.-]{0,99}$")] | None
    ) = None


class ToolPermission(BaseModel):
    model_config = _STRICT

    tool: ToolName
    access: Literal["read", "write"] = "read"


class ExecutionLimits(BaseModel):
    model_config = _STRICT

    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_tokens_per_task: int | None = Field(default=None, ge=1, le=2_000_000)


class AgentConfiguration(BaseModel):
    """Version 1 of the agent configuration schema. Stored verbatim in agent_versions."""

    model_config = _STRICT

    schema_version: Literal[1] = 1
    # A reference to versioned instructions (e.g. "builtin://researcher/v1"), not the text.
    system_instruction_ref: Annotated[
        str, StringConstraints(pattern=r"^[a-z]+://[A-Za-z0-9._/-]{1,200}$")
    ]
    model: ModelSettings = ModelSettings()
    capabilities: Annotated[list[Capability], Field(min_length=1, max_length=20)]
    tools: Annotated[list[ToolPermission], Field(max_length=20)] = []
    input_schema_version: int = Field(default=1, ge=1, le=1000)
    output_schema_version: int = Field(default=1, ge=1, le=1000)
    limits: ExecutionLimits = ExecutionLimits()

    @model_validator(mode="after")
    def _no_duplicates(self) -> "AgentConfiguration":
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        names = [permission.tool for permission in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("each tool may be granted once")
        return self

    def tool_names(self) -> list[str]:
        return [permission.tool for permission in self.tools]


class CatalogTool(BaseModel):
    name: str
    risk: str
    description: str
    available_in_phase_1: bool
    # Implemented and enabled now (ADR-0019); granting an unusable tool records a permission only.
    usable_now: bool


class AgentCatalogResponse(BaseModel):
    capabilities: list[str]
    categories: list[str]
    tools: list[CatalogTool]


# --- requests -------------------------------------------------------------------------------


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    category: Category
    description: Description = ""
    configuration: AgentConfiguration


class AgentUpdateRequest(BaseModel):
    """Edits the draft. Only `configuration` is versioned; it takes effect when published."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    name: AgentName | None = None
    category: Category | None = None
    description: Description | None = None
    configuration: AgentConfiguration | None = None


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)


class ProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    category: Category
    description: Description = ""
    rationale: Annotated[SafeText, StringConstraints(min_length=1, max_length=2000)]
    configuration: AgentConfiguration


class ProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Annotated[SafeText, StringConstraints(max_length=2000)] = ""


# --- responses ------------------------------------------------------------------------------


class AgentResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    is_builtin: bool
    name: str
    category: str
    description: str
    status: AgentStatus
    revision: int
    active_version: int | None
    draft_configuration: dict[str, Any] | None
    has_unpublished_changes: bool
    created_at: datetime
    updated_at: datetime


class AgentVersionResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    configuration: dict[str, Any]
    created_at: datetime
    created_by: uuid.UUID | None


class ProposalResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    category: str
    description: str
    rationale: str
    proposed_configuration: dict[str, Any]
    status: AgentProposalStatus
    requested_by: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    agent_id: uuid.UUID | None
    # Set for platform proposals (ADR-0024): the workflow task that had no suitable agent.
    source_workflow_id: uuid.UUID | None
    source_task_id: uuid.UUID | None
    created_at: datetime
