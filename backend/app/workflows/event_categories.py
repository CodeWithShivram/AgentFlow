"""Categories of workflow events (ADR-0042), for filtering the timeline. Pure, importable by the
API. Every event type belongs to exactly one category; a test keeps the mapping complete."""

from typing import Literal

from app.models import WorkflowEventType as E

EventCategory = Literal["status", "planning", "verification", "people", "recovery"]

CATEGORIES: dict[E, EventCategory] = {
    # Workflow and task lifecycle.
    E.WORKFLOW_CREATED: "status",
    E.WORKFLOW_STATUS_CHANGED: "status",
    E.WORKFLOW_COMPLETED: "status",
    E.WORKFLOW_FAILED: "status",
    E.WORKFLOW_CANCELLED: "status",
    E.TASK_CREATED: "status",
    E.TASK_UPDATED: "status",
    E.TASK_REMOVED: "status",
    E.TASK_ASSIGNED: "status",
    E.TASK_QUEUED: "status",
    E.TASK_STARTED: "status",
    E.TASK_STATUS_CHANGED: "status",
    E.TASK_COMPLETED: "status",
    E.DEPENDENCY_ADDED: "status",
    E.DEPENDENCY_REMOVED: "status",
    # Analysis and plans.
    E.ANALYSIS_REQUESTED: "planning",
    E.REQUIREMENTS_ANALYZED: "planning",
    E.ANALYSIS_FAILED: "planning",
    E.PLAN_REQUESTED: "planning",
    E.PLAN_GENERATED: "planning",
    E.PLAN_GENERATION_FAILED: "planning",
    E.PLAN_CREATED: "planning",
    E.PLAN_CONFIRMED: "planning",
    # Submissions and their checks.
    E.AGENT_SUBMITTED: "verification",
    E.VERIFICATION_STARTED: "verification",
    E.VERIFICATION_PASSED: "verification",
    E.VERIFICATION_FAILED: "verification",
    # Requests for people and their decisions.
    E.APPROVAL_REQUESTED: "people",
    E.APPROVAL_RESOLVED: "people",
    E.INTERVENTION_OPENED: "people",
    E.INTERVENTION_RESOLVED: "people",
    # Retries, reassignment and requeued jobs.
    E.RETRY_REQUESTED: "recovery",
    E.TASK_REASSIGNED: "recovery",
    E.BACKGROUND_JOB_RETRIED: "recovery",
}


def event_types(category: EventCategory) -> list[E]:
    return [event_type for event_type, value in CATEGORIES.items() if value == category]
