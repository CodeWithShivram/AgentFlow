"""Workflow and task API models (ADR-0009). Budget naming follows OD-5: `budget_limit`."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.models import AutonomyPolicy, TaskStatus, WorkflowEventType, WorkflowStatus
from app.schemas.agents import Capability, SafeText, Slug

_FORBID = ConfigDict(extra="forbid")
# Matches workflows.budget_limit NUMERIC(12, 4); the cap keeps values well inside it.
BudgetLimit = Annotated[
    Decimal,
    Field(ge=0, le=Decimal("1000000"), max_digits=12, decimal_places=4, allow_inf_nan=False),
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{3}$", to_upper=True)]
Reason = Annotated[SafeText, StringConstraints(max_length=500)]
MAX_PROMPT_LENGTH = 20_000


def _json_size_limit(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is not None and len(json.dumps(value)) > 20_000:
        raise ValueError("expected_output_schema is too large (max 20,000 characters)")
    return value


class WorkflowSettings(BaseModel):
    model_config = _FORBID

    autonomy_policy: AutonomyPolicy = AutonomyPolicy.APPROVAL_REQUIRED
    budget_limit: BudgetLimit | None = None
    currency: Currency = "USD"
    max_parallel_tasks: int = Field(default=3, ge=1, le=100)
    allow_dynamic_agent_proposals: bool = False
    deadline: AwareDatetime | None = None

    @field_validator("deadline")
    @classmethod
    def _deadline_in_future(cls, value: datetime | None) -> datetime | None:
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("deadline must be in the future")
        return value


class WorkflowCreateRequest(BaseModel):
    model_config = _FORBID

    prompt: Annotated[str, StringConstraints(min_length=1, max_length=MAX_PROMPT_LENGTH)]
    settings: WorkflowSettings = WorkflowSettings()

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class WorkflowTransitionRequest(BaseModel):
    model_config = _FORBID

    to_status: WorkflowStatus
    revision: int = Field(ge=1)
    reason: Reason | None = None


class RevisionReasonRequest(BaseModel):
    model_config = _FORBID

    revision: int = Field(ge=1)
    reason: Reason | None = None


class AcceptanceCriterion(BaseModel):
    model_config = _FORBID

    id: Slug
    description: Annotated[SafeText, StringConstraints(min_length=1, max_length=2000)]


Title = Annotated[SafeText, StringConstraints(min_length=1, max_length=300, strip_whitespace=True)]
Description = Annotated[SafeText, StringConstraints(max_length=20_000)]
Skills = Annotated[list[Capability], Field(max_length=20)]
InputReferences = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, max_length=500)]], Field(max_length=50)
]
Criteria = Annotated[list[AcceptanceCriterion], Field(max_length=50)]
OutputSchema = Annotated[dict[str, Any] | None, AfterValidator(_json_size_limit)]


class TaskCreateRequest(BaseModel):
    model_config = _FORBID

    title: Title
    description: Description = ""
    required_skills: Skills = []
    input_references: InputReferences = []
    expected_output_schema: OutputSchema = None
    acceptance_criteria: Criteria = []
    priority: int = Field(default=0, ge=-100, le=100)
    required: bool = True
    depends_on: Annotated[list[uuid.UUID], Field(max_length=50)] = []


class TaskUpdateRequest(BaseModel):
    model_config = _FORBID

    revision: int = Field(ge=1)
    title: Title | None = None
    description: Description | None = None
    required_skills: Skills | None = None
    input_references: InputReferences | None = None
    expected_output_schema: OutputSchema = None
    acceptance_criteria: Criteria | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)
    required: bool | None = None


class DependencyRequest(BaseModel):
    model_config = _FORBID

    depends_on_task_id: uuid.UUID


class AssignRequest(BaseModel):
    model_config = _FORBID

    agent_id: uuid.UUID
    revision: int = Field(ge=1)


# --- responses ------------------------------------------------------------------------------


class WorkflowResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: uuid.UUID
    prompt: str
    status: WorkflowStatus
    autonomy_policy: AutonomyPolicy
    budget_limit: Decimal | None
    currency: str
    max_parallel_tasks: int
    allow_dynamic_agent_proposals: bool
    deadline: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    title: str
    description: str
    required_skills: list[str]
    input_references: list[Any]
    expected_output_schema: dict[str, Any] | None
    acceptance_criteria: list[Any]
    priority: int
    required: bool
    removed: bool
    status: TaskStatus
    depends_on: list[uuid.UUID]
    assigned_agent_id: uuid.UUID | None
    assigned_agent_version_id: uuid.UUID | None
    attempt_count: int
    revision: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowEventResponse(BaseModel):
    id: uuid.UUID
    sequence: int
    task_id: uuid.UUID | None
    event_type: WorkflowEventType
    payload: dict[str, Any]
    created_at: datetime


class EventPage(BaseModel):
    items: list[WorkflowEventResponse]
    # Pass as `after` to continue; null when there are no events yet.
    last_sequence: int | None
