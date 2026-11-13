"""The interface every agent implements (handoff section 7, ADR-0018).

Agents run in Celery workers, outside any database transaction, and are synchronous like the
model gateway (ADR-0013). An agent receives a `TaskContract`, returns a result, and never
touches the database or task status: the runner validates and stores the submission.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import final

from app.agents.contract import TaskContract, ValidationResult, validate_submission
from app.schemas.agents import AgentConfiguration
from app.tools.gateway import ToolGateway


@dataclass(frozen=True)
class AgentContext:
    """Runtime identifiers for tracing and usage records, and the only way to call tools."""

    execution_id: uuid.UUID
    dispatch_event_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    agent_version: int
    # Enforces the contract's allowed tools on every call (ADR-0019).
    tools: ToolGateway


@dataclass(frozen=True)
class AgentCapabilities:
    capabilities: frozenset[str]
    tools: frozenset[str]


class BaseAgent(ABC):
    """One agent version's runtime. Built from its immutable, validated configuration."""

    def __init__(self, configuration: AgentConfiguration) -> None:
        self.configuration = configuration

    @abstractmethod
    def execute(self, contract: TaskContract, context: AgentContext) -> object:
        """Does the work and returns a submission (or data that should parse as one)."""

    def get_capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            capabilities=frozenset(self.configuration.capabilities),
            tools=frozenset(self.configuration.tool_names()),
        )

    @final
    def validate_output(self, contract: TaskContract, result: object) -> ValidationResult:
        """Platform-owned deterministic checks; agents cannot weaken them."""
        return validate_submission(contract, result)
