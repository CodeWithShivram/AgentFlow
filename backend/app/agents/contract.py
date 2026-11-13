"""The task contract an agent receives and the submission it returns (ADR-0018).

Both are strict Pydantic models. A submission is untrusted agent output: it is validated here
before it is stored, and its criterion claims are only claims. Platform verification (Phase 5)
decides whether a task is accepted; agents never complete tasks.
"""

import json
import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.agents import SafeText, Slug
from app.schemas.workflows import AcceptanceCriterion

_STRICT = ConfigDict(extra="forbid", frozen=True)

# Stored per attempt in agent_executions.submission; generous, but bounded.
MAX_OUTPUTS_CHARS = 100_000

Text = Annotated[SafeText, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
Items = Annotated[list[Text], Field(max_length=20)]


class SecurityClassification(StrEnum):
    # Every task is INTERNAL until a classification policy exists.
    INTERNAL = "INTERNAL"


class ToolGrant(BaseModel):
    """A tool this attempt may call through the tool gateway, with the access it was granted."""

    model_config = _STRICT

    tool: str
    access: Literal["read", "write"]


class RetryPolicy(BaseModel):
    model_config = _STRICT

    max_attempts: int = Field(ge=1)
    attempt_number: int = Field(ge=1)


class TaskContract(BaseModel):
    """Everything an agent may know about one attempt (handoff section 5.3). Built by the
    worker from the task, its pinned agent version and the workflow; never from agent input."""

    model_config = _STRICT

    contract_version: Literal[1] = 1
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    objective: str
    context: str
    input_references: list[str]
    required_skills: list[str]
    deliverables: list[str]
    output_schema: dict[str, Any] | None
    acceptance_criteria: list[AcceptanceCriterion]
    # The tasks this one depends on; all are COMPLETED before it is dispatched.
    dependencies: list[uuid.UUID]
    # Granted to the agent version, enabled and implemented on the platform, with enough access.
    # The tool gateway enforces this list on every call (ADR-0019).
    allowed_tools: list[ToolGrant]
    timeout_seconds: int
    token_budget: int | None
    retry_policy: RetryPolicy
    security_classification: SecurityClassification = SecurityClassification.INTERNAL


class CriterionStatus(StrEnum):
    MET = "MET"
    PARTIAL = "PARTIAL"
    NOT_MET = "NOT_MET"


class CriterionClaim(BaseModel):
    """The agent's own view of one acceptance criterion. Verification checks it independently."""

    model_config = _STRICT

    criterion_id: Slug
    status: CriterionStatus
    evidence: Text


def _outputs_size_limit(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value)) > MAX_OUTPUTS_CHARS:
        raise ValueError(f"outputs are too large (max {MAX_OUTPUTS_CHARS:,} characters)")
    return value


class AgentSubmission(BaseModel):
    """Version 1 of the structured result of one attempt. Stored verbatim per attempt."""

    model_config = _STRICT

    schema_version: Literal[1] = 1
    summary: Annotated[
        SafeText, StringConstraints(min_length=1, max_length=4000, strip_whitespace=True)
    ]
    # Deliverable content keyed by name. JSON Schema conformance is checked by verification.
    outputs: Annotated[dict[str, Any], AfterValidator(_outputs_size_limit)] = {}
    criteria: Annotated[list[CriterionClaim], Field(max_length=50)] = []
    assumptions: Items = []
    limitations: Items = []
    open_questions: Items = []


class ValidationResult(BaseModel):
    """Deterministic check of agent output against its contract. Issue codes are safe to store."""

    model_config = _STRICT

    submission: AgentSubmission | None
    issues: list[str]

    @property
    def valid(self) -> bool:
        return self.submission is not None and not self.issues


def validate_submission(contract: TaskContract, result: object) -> ValidationResult:
    """Parses `result` and checks there is exactly one claim per acceptance criterion."""
    try:
        submission = AgentSubmission.model_validate(
            result.model_dump() if isinstance(result, AgentSubmission) else result
        )
    except ValueError:  # pydantic.ValidationError; its text may echo agent output
        return ValidationResult(submission=None, issues=["SCHEMA_INVALID"])
    claimed = [claim.criterion_id for claim in submission.criteria]
    expected = {criterion.id for criterion in contract.acceptance_criteria}
    issues: list[str] = []
    if len(set(claimed)) != len(claimed):
        issues.append("DUPLICATE_CRITERION_CLAIM")
    if expected - set(claimed):
        issues.append("MISSING_CRITERION_CLAIM")
    if set(claimed) - expected:
        issues.append("UNKNOWN_CRITERION")
    return ValidationResult(submission=submission, issues=issues)
