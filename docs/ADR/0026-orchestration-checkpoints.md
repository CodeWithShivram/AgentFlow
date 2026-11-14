# ADR-0026: PostgreSQL checkpoints for the orchestration graph

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 4, Step 4.2 (PostgreSQL checkpoint persistence)
- Builds on: ADR-0006 (Alembic only), ADR-0010 (worker role), ADR-0012 (RLS), ADR-0025 (graph)
- Owner decisions (2026-11-14):
  - use the library's checkpointer on tables our migration creates;
  - keep the latest 20 checkpoints per workflow.

## Decisions

### Dependency and schema
- **Version.** `langgraph-checkpoint-postgres` 3.2.1, plus `psycopg-pool`, locked in `uv.lock`. Its `PostgresSaver` is used as is.
- **Tables from our migration.** Migration `0016_orchestration_checkpoints` creates the library's four tables (`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) with exactly its columns and primary keys. The thread indexes follow our naming. The migration records the library's 10 migrations as applied, so its `setup()` is never needed, and would change nothing (tested).
- **Models.** SQLAlchemy models (`app/models/checkpoint.py`) mirror the tables, so `alembic check` covers them.
- **Version pin.** A unit test pins the library's migration count and columns, so an upgrade that expects a different schema fails until a new Alembic migration exists.

### Access
- **Workers only.** The API role gets no grants on these tables (`API_EXCLUDED_TABLES`).
- **Row-level security.** RLS is enabled and forced on the three data tables, with only the platform policy and no tenant policy. Even a mistaken grant would show tenants nothing.
- **Worker grants.** The worker gets SELECT, INSERT, UPDATE (the library upserts) and DELETE (retention) on the data tables, and nothing on `checkpoint_migrations`.
- **No secrets or prompts.** The stored state is identifiers, statuses and counts (ADR-0025). A test confirms the workflow's prompt never appears in checkpoints or blobs.

### Use
- **Atomic with the assessment.** The orchestration job builds a `PostgresSaver` on its own session's psycopg connection, inside the transaction that holds the workflow lock. Checkpoints therefore commit, or roll back, together with the stored assessment.
- **One thread per workflow.** The thread is the workflow id (namespace `""`), and the graph is compiled with the checkpointer for each run.
- **Traceability.** `workflows.next_step.checkpoint_id` records the checkpoint of the latest assessment.

### Retention
The hourly cleanup (ADR-0010) gains three categories, each in bounded batches like the others:

| Category | Deletes |
|---|---|
| `checkpoints` | Everything beyond each thread's 20 newest (ids are time-ordered), and every checkpoint of a workflow that no longer exists. |
| `checkpoint_writes` | Writes whose checkpoint is gone. |
| `checkpoint_blobs` | Blobs (channel, version) that no remaining checkpoint's `channel_versions` refers to. |

## Consequences
- **Resumable later.** Later acting nodes (Phase 5) and pauses for people can resume from the stored thread. Step 4.5 (restart recovery) builds on this.
- **Upgrades.** Upgrading the library is a deliberate step: the pin test fails first, and a new migration mirrors any schema change.
- **Not adopted.** The library's `ShallowPostgresSaver` would keep only the latest checkpoint, but loses the history we decided to keep.
