"""The checkpoint tables match the installed langgraph-checkpoint-postgres (Step 4.2, ADR-0026).

Our Alembic migration creates the library's tables and records its migrations as applied; the
library's own setup() is never called. If an upgrade adds migrations (a schema change), this
fails, and the change must come through a new Alembic migration first.
"""

import importlib.util
from pathlib import Path

from langgraph.checkpoint.postgres.base import MIGRATIONS

from app.models import Base
from app.models.checkpoint import CHECKPOINT_SCHEMA_VERSIONS


def test_the_library_schema_version_is_the_one_our_migration_records() -> None:
    assert len(MIGRATIONS) == CHECKPOINT_SCHEMA_VERSIONS
    path = Path(__file__).parents[2] / "alembic" / "versions" / "0016_orchestration_checkpoints.py"
    spec = importlib.util.spec_from_file_location("migration_0016", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.LIBRARY_SCHEMA_VERSIONS == CHECKPOINT_SCHEMA_VERSIONS


def test_every_library_column_exists_in_our_models() -> None:
    ddl = " ".join(MIGRATIONS)
    expected = {
        "checkpoints": [
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "parent_checkpoint_id",
            "type",
            "checkpoint",
            "metadata",
        ],
        "checkpoint_blobs": ["thread_id", "checkpoint_ns", "channel", "version", "type", "blob"],
        "checkpoint_writes": [
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            "channel",
            "type",
            "blob",
            "task_path",
        ],
        "checkpoint_migrations": ["v"],
    }
    for table, columns in expected.items():
        assert f"TABLE IF NOT EXISTS {table}" in ddl or f"TABLE {table}" in ddl
        assert {c.name for c in Base.metadata.tables[table].columns} == set(columns), table
