"""core schema

Users, workspaces, membership, agents and versions, workflows, tasks, dependencies,
append-only workflow events and idempotency keys. See ADR-0006.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 15:52:21.837424+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_DEFAULT = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")

WORKSPACE_ROLES = ("OWNER", "ADMIN", "MEMBER", "VIEWER")
AGENT_STATUSES = ("ENABLED", "DISABLED")
WORKFLOW_STATUSES = ("PENDING", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED")
TASK_STATUSES = (
    "PENDING",
    "READY",
    "ASSIGNED",
    "QUEUED",
    "IN_PROGRESS",
    "SUBMITTED",
    "VERIFYING",
    "REWORK_REQUIRED",
    "WAITING_APPROVAL",
    "BLOCKED",
    "ESCALATED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
EVENT_TYPES = (
    "WORKFLOW_CREATED",
    "PLAN_CREATED",
    "TASK_CREATED",
    "TASK_ASSIGNED",
    "TASK_QUEUED",
    "TASK_STARTED",
    "AGENT_SUBMITTED",
    "VERIFICATION_STARTED",
    "VERIFICATION_PASSED",
    "VERIFICATION_FAILED",
    "RETRY_REQUESTED",
    "TASK_REASSIGNED",
    "APPROVAL_REQUESTED",
    "APPROVAL_RESOLVED",
    "TASK_COMPLETED",
    "WORKFLOW_COMPLETED",
    "WORKFLOW_FAILED",
    "WORKFLOW_CANCELLED",
)
IDEMPOTENCY_STATUSES = ("PROCESSING", "COMPLETED", "FAILED")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("settings", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_workspaces_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index(op.f("ix_workspaces_owner_id"), "workspaces", ["owner_id"])

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=6), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_in("role", WORKSPACE_ROLES), name=op.f("ck_workspace_members_role")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_members")),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name=op.f("uq_workspace_members_workspace_id_user_id")
        ),
    )
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"])

    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in("status", AGENT_STATUSES), name=op.f("ck_agents_status")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name=op.f("uq_agents_workspace_id_name"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_agents_status"), "agents", ["status"])

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_versions_version_positive")),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_agent_versions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "id", name=op.f("uq_agent_versions_agent_id_id")),
        sa.UniqueConstraint("agent_id", "version", name=op.f("uq_agent_versions_agent_id_version")),
    )
    # Added after both tables exist: agents <-> agent_versions reference each other.
    op.create_foreign_key(
        op.f("fk_agents_id_active_version_id_agent_versions"),
        "agents",
        "agent_versions",
        ["id", "active_version_id"],
        ["agent_id", "id"],
    )

    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("normalized_requirements", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("autonomy_policy", sa.String(length=50), nullable=False),
        sa.Column("budget_limit", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("max_parallel_tasks", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("status", WORKFLOW_STATUSES), name=op.f("ck_workflows_status")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_workflows_currency_iso4217")),
        sa.CheckConstraint(
            "budget_limit IS NULL OR budget_limit >= 0",
            name=op.f("ck_workflows_budget_non_negative"),
        ),
        sa.CheckConstraint(
            "max_parallel_tasks BETWEEN 1 AND 100",
            name=op.f("ck_workflows_max_parallel_tasks_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_workflows_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workflows_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_workflows_workspace_id_id")),
    )
    op.create_index(op.f("ix_workflows_created_by_user_id"), "workflows", ["created_by_user_id"])
    op.create_index(
        "ix_workflows_workspace_id_created_at", "workflows", ["workspace_id", "created_at"]
    )
    op.create_index("ix_workflows_workspace_id_status", "workflows", ["workspace_id", "status"])

    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "required_skills", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False
        ),
        sa.Column(
            "input_references", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False
        ),
        sa.Column("expected_output_schema", postgresql.JSONB(), nullable=True),
        sa.Column(
            "acceptance_criteria", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False
        ),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assigned_agent_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("status", TASK_STATUSES), name=op.f("ck_workflow_tasks_status")),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_workflow_tasks_attempt_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"],
            ["agents.id"],
            name=op.f("fk_workflow_tasks_assigned_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "parent_task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name=op.f("fk_workflow_tasks_workflow_id_parent_task_id_workflow_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name=op.f("fk_workflow_tasks_workspace_id_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_tasks")),
        sa.UniqueConstraint("workflow_id", "id", name=op.f("uq_workflow_tasks_workflow_id_id")),
    )
    op.create_index(
        op.f("ix_workflow_tasks_assigned_agent_id"), "workflow_tasks", ["assigned_agent_id"]
    )
    op.create_index(op.f("ix_workflow_tasks_parent_task_id"), "workflow_tasks", ["parent_task_id"])
    op.create_index(
        "ix_workflow_tasks_workflow_id_status", "workflow_tasks", ["workflow_id", "status"]
    )
    op.create_index(
        "ix_workflow_tasks_workspace_id_status", "workflow_tasks", ["workspace_id", "status"]
    )

    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_task_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id", name=op.f("ck_task_dependencies_no_self_dependency")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "depends_on_task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_task_dependencies_depends_on_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_task_dependencies_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "depends_on_task_id", name=op.f("pk_task_dependencies")),
    )
    op.create_index(
        op.f("ix_task_dependencies_depends_on_task_id"), "task_dependencies", ["depends_on_task_id"]
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            _in("event_type", EVENT_TYPES), name=op.f("ck_workflow_events_event_type")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name=op.f("fk_workflow_events_workflow_id_task_id_workflow_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name=op.f("fk_workflow_events_workspace_id_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_events")),
        sa.UniqueConstraint("sequence", name=op.f("uq_workflow_events_sequence")),
    )
    op.create_index(op.f("ix_workflow_events_task_id"), "workflow_events", ["task_id"])
    op.create_index(
        "ix_workflow_events_workflow_id_sequence", "workflow_events", ["workflow_id", "sequence"]
    )
    # Append-only history: reject UPDATE at the database level. DELETE stays possible for
    # workspace deletion (cascade) and retention jobs.
    op.execute(
        """
        CREATE FUNCTION workflow_events_reject_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'workflow_events is append-only; UPDATE is not allowed'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_events_append_only
        BEFORE UPDATE ON workflow_events
        FOR EACH ROW EXECUTE FUNCTION workflow_events_reject_update()
        """
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in("status", IDEMPOTENCY_STATUSES), name=op.f("ck_idempotency_keys_status")
        ),
        sa.CheckConstraint(
            "status = 'PROCESSING' OR response_status_code IS NOT NULL",
            name=op.f("ck_idempotency_keys_finished_has_response"),
        ),
        sa.CheckConstraint(
            "char_length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_idempotency_keys_expires_after_created")
        ),
        sa.CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name=op.f("ck_idempotency_keys_response_status_code_range"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_idempotency_keys_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint(
            "workspace_id",
            "operation",
            "idempotency_key",
            name=op.f("uq_idempotency_keys_workspace_id_operation_idempotency_key"),
        ),
    )
    op.create_index(op.f("ix_idempotency_keys_expires_at"), "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.execute("DROP TRIGGER workflow_events_append_only ON workflow_events")
    op.execute("DROP FUNCTION workflow_events_reject_update()")
    op.drop_table("workflow_events")
    op.drop_table("task_dependencies")
    op.drop_table("workflow_tasks")
    op.drop_table("workflows")
    op.drop_constraint(
        op.f("fk_agents_id_active_version_id_agent_versions"), "agents", type_="foreignkey"
    )
    op.drop_table("agent_versions")
    op.drop_table("agents")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
