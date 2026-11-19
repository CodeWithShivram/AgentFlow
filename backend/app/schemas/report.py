"""The workflow execution report (ADR-0042): composed from stored records only.

Agent and model text (deliverable contents, questions, proposed actions) is untrusted and shown
as plain text only. Verification appears as outcomes and issue codes, never evaluator findings;
people's free text (answers, rejection notes) is left out.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.schemas.analytics import WorkflowAnalyticsResponse
from app.schemas.costs import WorkflowCostsResponse


class ReportVerification(BaseModel):
    verifier: str
    outcome: str
    issue_codes: list[str]


class ReportAttempt(BaseModel):
    attempt_number: int
    agent_id: uuid.UUID
    agent_name: str
    agent_version: int
    status: str
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None
    verification: list[ReportVerification]


class ReportDeliverable(BaseModel):
    name: str
    # The accepted attempt's content (agent text: plain text only); None if it gave none.
    content: str | None
    truncated: bool


class ReportTask(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    required: bool
    removed: bool
    priority: int
    required_skills: list[str]
    depends_on: list[uuid.UUID]
    assigned_agent_id: uuid.UUID | None
    assigned_agent_name: str | None
    assigned_agent_version: int | None
    started_at: datetime | None
    completed_at: datetime | None
    attempts: list[ReportAttempt]
    # Completed tasks only, from the accepted (latest successful) attempt.
    deliverables: list[ReportDeliverable]


class ReportIntervention(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    type: str
    subject: str
    status: str
    # What was asked or proposed (may be model or agent text: plain text only).
    requested_action: str
    requested_by: str
    created_at: datetime
    resolved_at: datetime | None
    decision: str | None
    then: str | None
    outcome: str | None
    decided_role: str | None


class ReportPause(BaseModel):
    at: datetime
    pause_reason: str | None
    actor: str | None


class ReportRecovery(BaseModel):
    # Attempts after a task's first one.
    retries: int
    failed_attempts: int
    reassignments: int
    escalations: int
    expired_requests: int
    failed_background_jobs: int
    pauses: list[ReportPause]


class ReportMetrics(BaseModel):
    tasks: int
    tasks_by_status: dict[str, int]
    attempts: int
    attempts_by_status: dict[str, int]
    verifications_passed: int
    verifications_failed: int
    # From the workflow's own timestamps: until it finished, or until now while it runs.
    elapsed_seconds: float | None


class ReportWorkflow(BaseModel):
    id: uuid.UUID
    prompt: str
    status: str
    pause_reason: str | None
    autonomy_policy: str
    budget_limit: Decimal | None
    currency: str
    max_parallel_tasks: int
    deadline: datetime | None
    confirmed_plan_version: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowReport(BaseModel):
    report_version: str
    generated_at: datetime
    # True once the workflow is completed, failed or cancelled; until then, in progress.
    final: bool
    workflow: ReportWorkflow
    tasks: list[ReportTask]
    interventions: list[ReportIntervention]
    recovery: ReportRecovery
    metrics: ReportMetrics
    costs: WorkflowCostsResponse
    usage: WorkflowAnalyticsResponse
    # Requirements and analysis as stored (model output, validated; plain text only).
    requirements: dict[str, Any] | None
