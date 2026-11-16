"""Human intervention API models (ADR-0035).

Requested actions and contexts can hold model or agent text (a question from the analysis, an
agent's proposed action): clients show them as plain text only.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.models import InterventionStatus, InterventionType
from app.schemas.agents import SafeText


class InterventionResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID | None
    type: InterventionType
    # analysis_question, plan_approval, agent_action or task_escalation.
    subject: str
    status: InterventionStatus
    requested_action: str
    context: dict[str, Any]
    requested_by: Literal["SYSTEM", "AGENT", "USER"]
    requested_by_agent_id: uuid.UUID | None
    resolved_by_user_id: uuid.UUID | None
    # {"answer"}, {"decision"} or, when the work moved on, {"outcome"}.
    resolution: dict[str, Any] | None
    resolved_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    # The start of the workflow's prompt, so an inbox row says which work it is about.
    workflow_prompt: str


def _not_blank(value: str) -> str:
    # `strip_whitespace` runs after the length check, so a blank answer needs its own check.
    if not value:
        raise ValueError("The answer must not be blank.")
    return value


class RejectRequest(BaseModel):
    """A rejection (ADR-0037). `then` applies to an agent's request: "retry" (the agent finishes
    without the action) or "stop" (the task is escalated to people). The note is for people only
    and never reaches agents or models."""

    model_config = ConfigDict(extra="forbid")

    then: Literal["retry", "stop"] = "retry"
    note: Annotated[SafeText, StringConstraints(max_length=1000, strip_whitespace=True)] | None = (
        None
    )


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Annotated[
        SafeText,
        StringConstraints(max_length=4000, strip_whitespace=True),
        AfterValidator(_not_blank),
    ]
