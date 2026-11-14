"""orchestration checkpoints

Creates the LangGraph Postgres checkpoint tables (Phase 4, Step 4.2, ADR-0026) with the columns
and keys `langgraph-checkpoint-postgres` expects, and records the library's 10 schema versions as
applied, so its own `setup()` would change nothing. Only workers use them: row-level security
admits the platform role alone, and the API role gets no grants (app/db/roles.py).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 10:41:47.146127+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM = "agentflow_rls_platform"
LIBRARY_SCHEMA_VERSIONS = 10  # len(langgraph.checkpoint.postgres.base.MIGRATIONS) in 3.2.1
DATA_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint("v", name=op.f("pk_checkpoint_migrations")),
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", name=op.f("pk_checkpoints")
        ),
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "channel", "version", name=op.f("pk_checkpoint_blobs")
        ),
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            name=op.f("pk_checkpoint_writes"),
        ),
    )
    for table in DATA_TABLES:
        op.create_index(op.f(f"ix_{table}_thread_id"), table, ["thread_id"])

    # The library's own migrations are recorded as applied: its setup() would be a no-op.
    op.execute(
        "INSERT INTO checkpoint_migrations (v) "
        f"SELECT generate_series(0, {LIBRARY_SCHEMA_VERSIONS - 1})"
    )

    for table in DATA_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # No tenant policy: tenants (the API) can never see checkpoints, even if granted.
        op.execute(
            f"CREATE POLICY {table}_platform ON {table} FOR ALL TO {PLATFORM} "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in DATA_TABLES:
        op.execute(f"DROP POLICY {table}_platform ON {table}")
        op.drop_index(op.f(f"ix_{table}_thread_id"), table_name=table)
    op.drop_table("checkpoint_writes")
    op.drop_table("checkpoint_blobs")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_migrations")
