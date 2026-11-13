"""The tool registry (ADR-0019): how each catalog tool is run, and whether it can be.

The catalog (`app/agents/catalog.py`) is the policy agents are granted against. This registry
adds what the tool gateway needs at run time: the access a tool requires and, once its backend
exists, an implementation with strict argument and result schemas. A tool without an
implementation is unavailable and never reaches an agent's contract.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.catalog import TOOLS
from app.agents.contract import ToolGrant
from app.schemas.agents import AgentConfiguration

Access = Literal["read", "write"]


@dataclass(frozen=True)
class ToolCallContext:
    """Where a call belongs. Handlers scope everything they touch to this workspace and task."""

    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    execution_id: uuid.UUID


@dataclass(frozen=True)
class ToolImplementation:
    arguments: type[BaseModel]
    result: type[BaseModel]
    # Receives validated arguments; its result is validated again by the gateway.
    handler: Callable[[Any, ToolCallContext], BaseModel]
    max_result_chars: int = 20_000


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    requires: Access
    implementation: ToolImplementation | None = None

    @property
    def available(self) -> bool:
        spec = TOOLS.get(self.name)
        return self.implementation is not None and spec is not None and spec.available_in_phase_1


def access_covers(granted: Access, required: Access) -> bool:
    return granted == "write" or required == "read"


# Every catalog tool. None is implemented yet: workspace files and artifacts need artifact
# storage (Step 8.3), web search needs a configured provider, and code execution stays disabled.
REGISTRY: dict[str, ToolDefinition] = {
    definition.name: definition
    for definition in (
        ToolDefinition("document_retrieval", "read"),
        ToolDefinition("workspace_files_read", "read"),
        ToolDefinition("artifact_writer", "write"),
        ToolDefinition("workspace_files_write", "write"),
        ToolDefinition("web_search", "read"),
        ToolDefinition("python_analysis", "write"),
        ToolDefinition("test_runner", "write"),
    )
}


def usable_grants(
    configuration: AgentConfiguration, registry: Mapping[str, ToolDefinition] = REGISTRY
) -> list[ToolGrant]:
    """The version's grants that can run now: known, available, and with enough access."""
    grants: list[ToolGrant] = []
    for permission in configuration.tools:
        definition = registry.get(permission.tool)
        if (
            definition is not None
            and definition.available
            and access_covers(permission.access, definition.requires)
        ):
            grants.append(ToolGrant(tool=permission.tool, access=permission.access))
    return grants
