"""project packages

Adds `project_packages` (Step 9.6, ADR-0052): one append-only row per packaging attempt of a
software project, READY with its archive and build report artifacts or FAILED with a fixed code,
labelled VERIFIED or PARTIAL; row-level security as for every workspace-owned table. Also makes
`artifacts.deleted_by_user_id` RESTRICT instead of SET NULL: the artifacts trigger refuses updates
to deleted rows, so a SET NULL cascade could never succeed.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-26 12:37:02.828155+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_packages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "label",
            sa.Enum("VERIFIED", "PARTIAL", name="label", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("READY", "FAILED", name="status", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("archive_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("report_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("excluded_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'READY') = (archive_artifact_id IS NOT NULL AND error_code IS NULL)",
            name=op.f("ck_project_packages_ready_has_archive"),
        ),
        sa.CheckConstraint(
            "label = 'PARTIAL' OR integration_id IS NOT NULL",
            name=op.f("ck_project_packages_verified_has_integration"),
        ),
        sa.ForeignKeyConstraint(
            ["archive_artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_project_packages_archive_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["project_integrations.id"],
            name=op.f("fk_project_packages_integration_id_project_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_project_packages_report_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_project_packages_requested_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_project_packages_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_project_packages_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_packages")),
    )
    op.create_index(
        "ix_project_packages_workflow_created",
        "project_packages",
        ["workflow_id", "created_at"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_artifacts_deleted_by_user_id_users"), "artifacts", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_artifacts_deleted_by_user_id_users"),
        "artifacts",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE FUNCTION project_packages_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'project_packages rows are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER project_packages_immutable BEFORE UPDATE ON project_packages "
        "FOR EACH ROW EXECUTE FUNCTION project_packages_reject_update()"
    )
    op.execute("ALTER TABLE project_packages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE project_packages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY project_packages_tenant ON project_packages FOR ALL "
        "TO agentflow_rls_tenant USING (workspace_id IN (SELECT public.app_user_workspace_ids())) "
        "WITH CHECK (workspace_id IN (SELECT public.app_user_workspace_ids()))"
    )
    op.execute(
        "CREATE POLICY project_packages_platform ON project_packages FOR ALL "
        "TO agentflow_rls_platform USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY project_packages_platform ON project_packages")
    op.execute("DROP POLICY project_packages_tenant ON project_packages")
    op.execute("DROP TRIGGER project_packages_immutable ON project_packages")
    op.execute("DROP FUNCTION project_packages_reject_update()")
    op.drop_constraint(
        op.f("fk_artifacts_deleted_by_user_id_users"), "artifacts", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_artifacts_deleted_by_user_id_users"),
        "artifacts",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_index("ix_project_packages_workflow_created", table_name="project_packages")
    op.drop_table("project_packages")
