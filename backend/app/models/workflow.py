"""Workflows, their task DAG and the append-only event history.

Every row carries `workspace_id`, and composite foreign keys include it, so the database
itself rejects a task, dependency or event that points into another workspace or workflow.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, updated_at_column, uuid_pk


class WorkflowStatus(StrEnum):
    # OD-11, ADR-0009. Transitions live in app/workflows/state_machine.py.
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AnalysisStatus(StrEnum):
    # Prompt analysis by the model gateway (Step 2.2). Independent of the workflow status.
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class PlanStatus(StrEnum):
    # Plan generation by the model gateway (Step 2.3). Drafts are kept in workflow_plans.
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class AutonomyPolicy(StrEnum):
    # approval_required: a plan must be approved (AWAITING_APPROVAL) before it can run.
    # autonomous: a submitted plan goes straight to READY.
    APPROVAL_REQUIRED = "approval_required"
    AUTONOMOUS = "autonomous"


class TaskStatus(StrEnum):
    # Handoff section 6.
    PENDING = "PENDING"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    VERIFYING = "VERIFYING"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowEventType(StrEnum):
    # Handoff section 5.8.
    WORKFLOW_CREATED = "WORKFLOW_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_QUEUED = "TASK_QUEUED"
    TASK_STARTED = "TASK_STARTED"
    AGENT_SUBMITTED = "AGENT_SUBMITTED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RETRY_REQUESTED = "RETRY_REQUESTED"
    TASK_REASSIGNED = "TASK_REASSIGNED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    TASK_COMPLETED = "TASK_COMPLETED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    # Added in Step 1.7 for changes the handoff list does not name.
    WORKFLOW_STATUS_CHANGED = "WORKFLOW_STATUS_CHANGED"
    TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_REMOVED = "TASK_REMOVED"
    DEPENDENCY_ADDED = "DEPENDENCY_ADDED"
    DEPENDENCY_REMOVED = "DEPENDENCY_REMOVED"
    # Added in Step 2.2 for prompt analysis.
    ANALYSIS_REQUESTED = "ANALYSIS_REQUESTED"
    REQUIREMENTS_ANALYZED = "REQUIREMENTS_ANALYZED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    # Added in Step 2.3 for plan drafts.
    PLAN_REQUESTED = "PLAN_REQUESTED"
    PLAN_GENERATED = "PLAN_GENERATED"
    PLAN_GENERATION_FAILED = "PLAN_GENERATION_FAILED"
    # Added in Step 2.5: a draft became the workflow's tasks.
    PLAN_CONFIRMED = "PLAN_CONFIRMED"


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        # Target of composite foreign keys that pin children to the same workspace.
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint("budget_limit IS NULL OR budget_limit >= 0", name="budget_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("max_parallel_tasks BETWEEN 1 AND 100", name="max_parallel_tasks_range"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_workflows_workspace_id_created_at", "workspace_id", "created_at"),
        Index("ix_workflows_workspace_id_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    # The validated prompt analysis (app.planning.analysis.PromptAnalysis), once READY.
    normalized_requirements: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        str_enum(AnalysisStatus, "analysis_status"),
        server_default=text("'NOT_REQUESTED'"),
        default=AnalysisStatus.NOT_REQUESTED,
    )
    # The outbox event of the current analysis request; older deliveries are ignored.
    analysis_request_id: Mapped[uuid.UUID | None]
    plan_status: Mapped[PlanStatus] = mapped_column(
        str_enum(PlanStatus, "plan_status"),
        server_default=text("'NOT_REQUESTED'"),
        default=PlanStatus.NOT_REQUESTED,
    )
    # The outbox event of the current plan-generation request.
    plan_request_id: Mapped[uuid.UUID | None]
    # The draft version most recently confirmed into tasks (Step 2.5).
    confirmed_plan_version: Mapped[int | None]
    # The orchestration graph's latest assessment (ADR-0025): kind, actor, summary, task_ids,
    # assessed_at and graph_version. Written by workers; never bumps `revision`.
    next_step: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[WorkflowStatus] = mapped_column(
        str_enum(WorkflowStatus, "status"), default=WorkflowStatus.PENDING
    )
    autonomy_policy: Mapped[AutonomyPolicy] = mapped_column(
        str_enum(AutonomyPolicy, "autonomy_policy"), default=AutonomyPolicy.APPROVAL_REQUIRED
    )
    budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'USD'"))
    max_parallel_tasks: Mapped[int] = mapped_column(server_default=text("3"))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allow_dynamic_agent_proposals: Mapped[bool] = mapped_column(
        server_default=text("false"), default=False
    )
    # Optimistic concurrency for user edits; mutations also lock the row (ADR-0009).
    revision: Mapped[int] = mapped_column(server_default=text("1"), default=1)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        UniqueConstraint("workflow_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
        ),
        # A parent task must belong to the same workflow.
        ForeignKeyConstraint(
            ["workflow_id", "parent_task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
        ),
        # Execution references the exact agent version, which must belong to the agent.
        ForeignKeyConstraint(
            ["assigned_agent_id", "assigned_agent_version_id"],
            ["agent_versions.agent_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_tasks_assigned_agent_version",
        ),
        CheckConstraint(
            "(assigned_agent_id IS NULL) = (assigned_agent_version_id IS NULL)",
            name="agent_and_version_assigned_together",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        # A task removed from the plan stays as history, CANCELLED.
        CheckConstraint("removed_at IS NULL OR status = 'CANCELLED'", name="removed_is_cancelled"),
        Index("ix_workflow_tasks_workflow_id_status", "workflow_id", "status"),
        Index("ix_workflow_tasks_workspace_id_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID]
    workflow_id: Mapped[uuid.UUID]
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    input_references: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    expected_output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Expected deliverables, part of the task contract (ADR-0018).
    deliverables: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    acceptance_criteria: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    priority: Mapped[int] = mapped_column(server_default=text("0"))
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus, "status"), default=TaskStatus.PENDING
    )
    # Agents are disabled rather than deleted; RESTRICT protects assignment history.
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    # Pinned when assigned; later publishing never changes what this task runs.
    assigned_agent_version_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    attempt_count: Mapped[int] = mapped_column(server_default=text("0"))
    # Required tasks must be COMPLETED before the workflow can complete.
    required: Mapped[bool] = mapped_column(server_default=text("true"), default=True)
    # Set when the task is removed from the plan (rows are kept: events reference them).
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(server_default=text("1"), default=1)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDependency(Base):
    """`task_id` depends on `depends_on_task_id`. Both must be in the same workflow.

    Self-dependency is rejected here; multi-task cycles are rejected by the DAG validator.
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_task_dependencies_task",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "depends_on_task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            ondelete="CASCADE",
            name="fk_task_dependencies_depends_on_task",
        ),
        CheckConstraint("task_id <> depends_on_task_id", name="no_self_dependency"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, index=True)
    workflow_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = created_at_column()


class WorkflowEvent(Base):
    """Append-only. A database trigger rejects UPDATE; rows may only be deleted by retention."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
        ),
        Index("ix_workflow_events_workflow_id_sequence", "workflow_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Total order of events, stable even when timestamps are equal.
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    workspace_id: Mapped[uuid.UUID]
    workflow_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    event_type: Mapped[WorkflowEventType] = mapped_column(str_enum(WorkflowEventType, "event_type"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()
