"""Request, response and adapter contracts of the model gateway."""

import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Purpose = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ModelProfile = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class ModelRequest(BaseModel):
    """What the caller wants. The model itself is chosen by the gateway from the profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Why the call is made; recorded with the usage and used to look up mock responses.
    purpose: Purpose
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=200)]
    profile: ModelProfile = "default"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=200_000)


@dataclass(frozen=True)
class CallContext:
    """Where the usage of a call belongs. Every call belongs to a workspace."""

    workspace_id: uuid.UUID
    workflow_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProviderUsage:
    """Usage exactly as the provider reported it; None means "not reported", never estimated."""

    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None = None
    other: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResult:
    text: str
    usage: ProviderUsage
    provider_request_id: str | None = None


class ModelAdapter(Protocol):
    """One provider. Adapters translate requests and report usage; they never record it."""

    @property
    def provider(self) -> str: ...

    def complete(
        self, request: ModelRequest, *, model: str, response_format: type[BaseModel] | None
    ) -> AdapterResult: ...


@dataclass(frozen=True)
class ModelResponse[T: BaseModel]:
    text: str
    # The validated structured output when a response_format was requested.
    parsed: T | None
    usage: ProviderUsage
    provider: str
    model: str
    usage_id: uuid.UUID
    provider_request_id: str | None
