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

from app.schemas.agents import SafeText, Slug, not_blank
from app.schemas.workflows import AcceptanceCriterion

_STRICT = ConfigDict(extra="forbid", frozen=True)

# Stored per attempt in agent_executions.submission; generous, but bounded.
MAX_OUTPUTS_CHARS = 100_000

# Agent text is stripped and must not be blank: a value of only spaces is invalid output.
Text = Annotated[
    SafeText,
    StringConstraints(min_length=1, max_length=2000, strip_whitespace=True),
    AfterValidator(not_blank),
]
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


class DependencyFile(BaseModel):
    """An accepted file of a task this one depends on (ADR-0049)."""

    model_config = _STRICT

    path: str
    content: str


class IntegrationFeedback(BaseModel):
    """One failed integration check (ADR-0056): its name and capped, redacted output."""

    model_config = _STRICT

    check: str
    output: str


class TaskContract(BaseModel):
    """Everything an agent may know about one attempt (handoff section 5.3). Built by the
    worker from the task, its pinned agent version and the workflow; never from agent input."""

    model_config = _STRICT

    # v2 (ADR-0032): adds `rework_instructions`. v3 (ADR-0035): adds `approved_actions`.
    # v4 (ADR-0049): adds `file_scope`, `stack_profile` and `dependency_files`.
    # v5 (ADR-0056): adds `integration_feedback` and `current_files` for integration fix tasks.
    # v6 (ADR-0061): adds `starter_files`.
    contract_version: Literal[6] = 6
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
    # Corrective instructions from the previous attempt's failure: fixed platform sentences that
    # name criteria by id and deliverables by position (ADR-0032). Empty on a first attempt.
    rework_instructions: list[str] = []
    # Actions people approved after this task's previous attempt asked (ADR-0035). The text is
    # that agent's own description of the action: data, not an instruction.
    approved_actions: list[str] = []
    # Software project tasks only (ADR-0049): the files this task alone may write, the stack
    # profile, and the accepted files of the tasks it depends on (data, not instructions).
    file_scope: list[str] = []
    stack_profile: str | None = None
    dependency_files: list[DependencyFile] = []
    # Integration fix tasks only (ADR-0056): the failed checks' output (untrusted tool text,
    # data) and the current content of this task's scope in the project workspace.
    integration_feedback: list[IntegrationFeedback] = []
    current_files: list[DependencyFile] = []
    # The profile's minimal working versions of files in this task's scope (ADR-0061): platform
    # templates to build on, never agent or model text.
    starter_files: list[DependencyFile] = []


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


class ApprovalRequest(BaseModel):
    """An agent asking people to approve an action before it is taken (ADR-0035). The task waits
    for the decision; either way a new attempt follows. Agent text: shown as plain text only."""

    model_config = _STRICT

    action: Annotated[
        SafeText,
        StringConstraints(min_length=1, max_length=1000, strip_whitespace=True),
        AfterValidator(not_blank),
    ]
    reason: Text
    risks: Items = []
    consequences: Items = []


class AgentSubmission(BaseModel):
    """Version 1 of the structured result of one attempt. Stored verbatim per attempt."""

    model_config = _STRICT

    schema_version: Literal[1] = 1
    summary: Annotated[
        SafeText,
        StringConstraints(min_length=1, max_length=4000, strip_whitespace=True),
        AfterValidator(not_blank),
    ]
    # Deliverable content keyed by name. JSON Schema conformance is checked by verification.
    outputs: Annotated[dict[str, Any], AfterValidator(_outputs_size_limit)] = {}
    criteria: Annotated[list[CriterionClaim], Field(max_length=50)] = []
    assumptions: Items = []
    limitations: Items = []
    open_questions: Items = []
    # Set only when the agent needs a person's approval before it can finish (ADR-0035).
    approval_request: ApprovalRequest | None = None


class ValidationResult(BaseModel):
    """Deterministic check of agent output against its contract. Issue codes are safe to store."""

    model_config = _STRICT

    submission: AgentSubmission | None
    issues: list[str]

    @property
    def valid(self) -> bool:
        return self.submission is not None and not self.issues


class InvalidSubmissionError(Exception):
    """The agent's output failed the deterministic checks. Issue codes are safe to store."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__(", ".join(issues))
        self.issues = issues


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
