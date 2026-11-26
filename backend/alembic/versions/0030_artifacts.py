"""artifacts

Adds `artifacts`, the metadata of stored files (handoff section 10 `task_artifacts`; Step 9.1,
ADR-0047): workflow-scoped with an optional task and attempt, a platform-derived storage key,
size and SHA-256. Bytes live in local artifact storage, never in PostgreSQL. Row-level security
limits people to their workspaces; workers use the platform policy. A trigger keeps the file's
identity (key, size, checksum, links) immutable and allows only AVAILABLE -> DELETED.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-26 11:05:02.703227+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

TENANT = "agentflow_rls_tenant"
PLATFORM = "agentflow_rls_platform"
IN_USER_WORKSPACES = "workspace_id IN (SELECT public.app_user_workspace_ids())"

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "PROJECT_ARCHIVE",
                "BUILD_REPORT",
                "TASK_OUTPUT",
                name="kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=120), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "AVAILABLE", "DELETED", name="status", native_enum=False, create_constraint=True
            ),
            server_default=sa.text("'AVAILABLE'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'DELETED') = (deleted_at IS NOT NULL)",
            name=op.f("ck_artifacts_deleted_has_time"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_artifacts_sha256_hex")),
        sa.CheckConstraint(
            "execution_id IS NULL OR task_id IS NOT NULL",
            name=op.f("ck_artifacts_execution_has_task"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_artifacts_size_not_negative")),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"],
            ["users.id"],
            name=op.f("fk_artifacts_deleted_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name=op.f("fk_artifacts_execution_id_agent_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.id"],
            name="fk_artifacts_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_artifacts_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_artifacts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_artifacts_storage_key")),
    )
    op.create_index(
        "ix_artifacts_workflow_created",
        "artifacts",
        ["workspace_id", "workflow_id", "created_at"],
        unique=False,
    )

    # What a stored file is never changes; it can only be marked deleted, once.
    op.execute(
        """
        CREATE FUNCTION artifacts_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id, NEW.workspace_id, NEW.workflow_id, NEW.task_id, NEW.execution_id,
                NEW.kind, NEW.filename, NEW.media_type, NEW.storage_key, NEW.size_bytes,
                NEW.sha256, NEW.created_at)
               IS DISTINCT FROM
               (OLD.id, OLD.workspace_id, OLD.workflow_id, OLD.task_id, OLD.execution_id,
                OLD.kind, OLD.filename, OLD.media_type, OLD.storage_key, OLD.size_bytes,
                OLD.sha256, OLD.created_at)
               OR OLD.status <> 'AVAILABLE' THEN
                RAISE EXCEPTION 'artifacts rows only change from AVAILABLE to DELETED'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER artifacts_guard BEFORE UPDATE ON artifacts "
        "FOR EACH ROW EXECUTE FUNCTION artifacts_guard_update()"
    )

    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE artifacts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY artifacts_tenant ON artifacts FOR ALL TO {TENANT} "
        f"USING ({IN_USER_WORKSPACES}) WITH CHECK ({IN_USER_WORKSPACES})"
    )
    op.execute(
        f"CREATE POLICY artifacts_platform ON artifacts FOR ALL TO {PLATFORM} "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY artifacts_platform ON artifacts")
    op.execute("DROP POLICY artifacts_tenant ON artifacts")
    op.execute("DROP TRIGGER artifacts_guard ON artifacts")
    op.execute("DROP FUNCTION artifacts_guard_update()")
    op.drop_index("ix_artifacts_workflow_created", table_name="artifacts")
    op.drop_table("artifacts")
