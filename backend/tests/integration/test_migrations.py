"""Alembic migrations against fresh disposable databases."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, create_engine, inspect, text

from app.models import Base
from tests.integration.conftest import alembic_config, disposable_database

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "workspaces",
    "workspace_members",
    "agents",
    "agent_versions",
    "workflows",
    "workflow_tasks",
    "task_dependencies",
    "workflow_events",
    "idempotency_keys",
    "auth_sessions",
    "refresh_tokens",
    "workspace_invitations",
}


def _with_connection(url: URL, fn: Any) -> Any:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return fn(connection)
    finally:
        engine.dispose()


def _public_tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _schema_object_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    names: set[str] = set()
    for table in inspector.get_table_names():
        names.add(str(inspector.get_pk_constraint(table)["name"]))
        names.update(str(fk["name"]) for fk in inspector.get_foreign_keys(table))
        names.update(str(uq["name"]) for uq in inspector.get_unique_constraints(table))
        names.update(str(ck["name"]) for ck in inspector.get_check_constraints(table))
        names.update(str(ix["name"]) for ix in inspector.get_indexes(table))
    return names


def _model_object_names() -> set[str]:
    names: set[str] = set()
    for table in Base.metadata.tables.values():
        names.update(str(c.name) for c in table.constraints)
        names.update(str(i.name) for i in table.indexes)
    return names


def _append_only_trigger_exists(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = 'workflow_events_append_only'")
        ).scalar_one()
    )


def _reject_update_function_exists(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT count(*) FROM pg_proc WHERE proname = 'workflow_events_reject_update'")
        ).scalar_one()
    )


def test_fresh_upgrade_creates_complete_schema() -> None:
    with disposable_database() as url:
        command.upgrade(alembic_config(url), "head")

        def check(connection: Connection) -> None:
            assert _public_tables(connection) == EXPECTED_TABLES | {"alembic_version"}
            version = connection.execute(text("SELECT version_num FROM alembic_version"))
            assert (
                version.scalar_one()
                == ScriptDirectory.from_config(alembic_config(url)).get_current_head()
            )
            # Every constraint and index declared on the models exists under the same name.
            assert _model_object_names() <= _schema_object_names(connection)
            assert _append_only_trigger_exists(connection)

        _with_connection(url, check)


def test_migrated_schema_matches_models() -> None:
    with disposable_database() as url:
        command.upgrade(alembic_config(url), "head")

        def diff(connection: Connection) -> list[Any]:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return list(compare_metadata(context, Base.metadata))

        assert _with_connection(url, diff) == []


def test_downgrade_to_base_removes_everything() -> None:
    with disposable_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        def check(connection: Connection) -> None:
            assert _public_tables(connection) == {"alembic_version"}
            assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 0
            assert not _reject_update_function_exists(connection)

        _with_connection(url, check)


def test_rollback_and_reapply_produces_identical_schema() -> None:
    with disposable_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "head")
        first = _with_connection(url, _schema_object_names)

        command.downgrade(config, "base")
        command.upgrade(config, "head")

        assert _with_connection(url, _schema_object_names) == first
        assert _with_connection(url, _append_only_trigger_exists)


def test_every_revision_upgrades_and_downgrades_one_step() -> None:
    """Stairway test: each revision can be applied, reverted and re-applied in order."""
    with disposable_database() as url:
        config = alembic_config(url)
        revisions = list(reversed(list(ScriptDirectory.from_config(config).walk_revisions())))
        assert revisions, "no migrations found"
        for revision in revisions:
            command.upgrade(config, revision.revision)
            command.downgrade(config, "-1")
            command.upgrade(config, revision.revision)


def test_downgrade_succeeds_with_data_present() -> None:
    with disposable_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "head")

        def seed(connection: Connection) -> None:
            connection.execute(
                text(
                    """
                    WITH u AS (
                        INSERT INTO users (email, password_hash) VALUES ('a@example.com', 'x')
                        RETURNING id
                    ), w AS (
                        INSERT INTO workspaces (owner_id, name) SELECT id, 'w' FROM u
                        RETURNING id, owner_id
                    )
                    INSERT INTO workflows (workspace_id, created_by_user_id, prompt, status,
                                           autonomy_policy)
                    SELECT id, owner_id, 'p', 'PENDING', 'approval_required' FROM w
                    """
                )
            )
            connection.commit()

        _with_connection(url, seed)
        command.downgrade(config, "base")

        assert _with_connection(url, _public_tables) == {"alembic_version"}
