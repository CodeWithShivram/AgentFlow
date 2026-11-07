"""agent registry

Agent drafts, revisions and descriptions; ACTIVE/INACTIVE status; immutable versions;
dynamic agent proposals; append-only registry audit events; tasks pinned to an exact agent
version. See ADR-0008.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 18:10:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_DEFAULT = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
AUDIT_ACTIONS = (
    "CREATED",
    "DRAFT_UPDATED",
    "VERSION_PUBLISHED",
    "ACTIVATED",
    "DEACTIVATED",
    "PROPOSAL_SUBMITTED",
    "PROPOSAL_APPROVED",
    "PROPOSAL_REJECTED",
)


def _reject_update_trigger(table: str, function: str, message: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '{message}' USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
    )


def upgrade() -> None:
    # --- agents: draft, revision, description; ENABLED/DISABLED -> ACTIVE/INACTIVE ----------
    op.add_column(
        "agents", sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.add_column("agents", sa.Column("draft_configuration", postgresql.JSONB(), nullable=True))
    op.add_column(
        "agents", sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    op.drop_constraint(op.f("ck_agents_status"), "agents", type_="check")
    op.execute(
        "UPDATE agents SET status = CASE status WHEN 'ENABLED' THEN 'ACTIVE' ELSE 'INACTIVE' END"
    )
    op.create_check_constraint(
        op.f("ck_agents_status"), "agents", "status IN ('ACTIVE', 'INACTIVE')"
    )
    op.create_check_constraint(op.f("ck_agents_revision_positive"), "agents", "revision >= 1")
    op.create_check_constraint(
        op.f("ck_agents_active_requires_version"),
        "agents",
        "status = 'INACTIVE' OR active_version_id IS NOT NULL",
    )

    # --- immutable published versions --------------------------------------------------------
    _reject_update_trigger(
        "agent_versions",
        "agent_versions_reject_update",
        "agent_versions are immutable; publish a new version instead",
    )

    # --- dynamic agent proposals ---------------------------------------------------------------
    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("proposed_configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')", name=op.f("ck_agent_proposals_status")
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (reviewed_at IS NULL)",
            name=op.f("ck_agent_proposals_decision_has_timestamp"),
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR agent_id IS NOT NULL",
            name=op.f("ck_agent_proposals_approval_has_agent"),
        ),
        sa.CheckConstraint(
            "reviewed_by IS NULL OR requested_by IS NULL OR reviewed_by <> requested_by",
            name=op.f("ck_agent_proposals_reviewer_not_requester"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agent_proposals_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_agent_proposals_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_agent_proposals_reviewed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_proposals_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_proposals")),
    )
    op.create_index(
        "ix_agent_proposals_workspace_id_status", "agent_proposals", ["workspace_id", "status"]
    )
    # Proposed content is frozen at submission; only the decision columns may change.
    op.execute(
        """
        CREATE FUNCTION agent_proposals_freeze_content() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.name IS DISTINCT FROM OLD.name
               OR NEW.category IS DISTINCT FROM OLD.category
               OR NEW.description IS DISTINCT FROM OLD.description
               OR NEW.rationale IS DISTINCT FROM OLD.rationale
               OR NEW.proposed_configuration IS DISTINCT FROM OLD.proposed_configuration
               OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
                RAISE EXCEPTION 'proposed agent content cannot change after submission'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status <> 'PENDING' THEN
                RAISE EXCEPTION 'proposal has already been decided'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER agent_proposals_frozen BEFORE UPDATE ON agent_proposals "
        "FOR EACH ROW EXECUTE FUNCTION agent_proposals_freeze_content()"
    )

    # --- append-only registry audit ------------------------------------------------------------
    op.create_table(
        "agent_audit_events",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=18), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "action IN (" + ", ".join(f"'{a}'" for a in AUDIT_ACTIONS) + ")",
            name=op.f("ck_agent_audit_events_action"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agent_audit_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_audit_events_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["agent_proposals.id"],
            name=op.f("fk_agent_audit_events_proposal_id_agent_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_agent_audit_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_audit_events")),
    )
    op.create_index(
        "ix_agent_audit_events_agent_id_created_at",
        "agent_audit_events",
        ["agent_id", "created_at"],
    )
    op.create_index(
        "ix_agent_audit_events_workspace_id_created_at",
        "agent_audit_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        op.f("ix_agent_audit_events_proposal_id"), "agent_audit_events", ["proposal_id"]
    )
    _reject_update_trigger(
        "agent_audit_events",
        "agent_audit_events_reject_update",
        "agent_audit_events is append-only; UPDATE is not allowed",
    )

    # --- tasks pin the exact agent version ----------------------------------------------------
    op.add_column(
        "workflow_tasks", sa.Column("assigned_agent_version_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        op.f("ix_workflow_tasks_assigned_agent_version_id"),
        "workflow_tasks",
        ["assigned_agent_version_id"],
    )
    op.create_foreign_key(
        "fk_workflow_tasks_assigned_agent_version",
        "workflow_tasks",
        "agent_versions",
        ["assigned_agent_id", "assigned_agent_version_id"],
        ["agent_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_workflow_tasks_agent_and_version_assigned_together"),
        "workflow_tasks",
        "(assigned_agent_id IS NULL) = (assigned_agent_version_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_workflow_tasks_agent_and_version_assigned_together"),
        "workflow_tasks",
        type_="check",
    )
    op.drop_constraint(
        "fk_workflow_tasks_assigned_agent_version", "workflow_tasks", type_="foreignkey"
    )
    op.drop_index(op.f("ix_workflow_tasks_assigned_agent_version_id"), table_name="workflow_tasks")
    op.drop_column("workflow_tasks", "assigned_agent_version_id")

    op.drop_table("agent_audit_events")
    op.execute("DROP FUNCTION agent_audit_events_reject_update()")
    op.drop_table("agent_proposals")
    op.execute("DROP FUNCTION agent_proposals_freeze_content()")

    op.execute("DROP TRIGGER agent_versions_immutable ON agent_versions")
    op.execute("DROP FUNCTION agent_versions_reject_update()")

    op.drop_constraint(op.f("ck_agents_active_requires_version"), "agents", type_="check")
    op.drop_constraint(op.f("ck_agents_revision_positive"), "agents", type_="check")
    op.drop_constraint(op.f("ck_agents_status"), "agents", type_="check")
    op.execute(
        "UPDATE agents SET status = CASE status WHEN 'ACTIVE' THEN 'ENABLED' ELSE 'DISABLED' END"
    )
    op.create_check_constraint(
        op.f("ck_agents_status"), "agents", "status IN ('ENABLED', 'DISABLED')"
    )
    op.drop_column("agents", "revision")
    op.drop_column("agents", "draft_configuration")
    op.drop_column("agents", "description")
