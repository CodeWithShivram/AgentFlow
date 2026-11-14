"""LangGraph checkpoint tables (ADR-0026), as `langgraph-checkpoint-postgres` reads and writes them.

The library normally creates these with its own `setup()`; here they are created by an Alembic
migration (ADR-0006) with the same columns and keys, and `setup()` is never called. A test pins
the library's migration list, so an upgrade that expects a different schema fails loudly.

Only workers touch them. The API has no grants, and row-level security admits only the platform
role. `thread_id` is the workflow id; the stored state holds identifiers and statuses only.
"""

from sqlalchemy import Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# The number of entries in langgraph.checkpoint.postgres.base.MIGRATIONS this schema matches.
CHECKPOINT_SCHEMA_VERSIONS = 10


class CheckpointMigration(Base):
    __tablename__ = "checkpoint_migrations"

    v: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("''"))
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, object]] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )


class CheckpointBlob(Base):
    __tablename__ = "checkpoint_blobs"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("''"))
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary)


class CheckpointWrite(Base):
    __tablename__ = "checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("''"))
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    task_path: Mapped[str] = mapped_column(Text, server_default=text("''"))
