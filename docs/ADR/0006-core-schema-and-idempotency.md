# ADR-0006: Core schema conventions, workspace isolation and idempotency keys

- Status: Accepted
- Date: 2026-11-07
- Resolves: OD-4 (idempotency key storage)

## Context
Step 1.4 introduces the first persistent schema: users, workspaces, membership, agents and versions, workflows, tasks, dependencies, workflow events, and idempotency keys. The handoff requires UUID keys, UTC timestamps, workspace scoping on every resource, append-only events, and idempotency for workflow creation and approval actions. Its schema (section 10) has no idempotency storage.

## Decisions

### Conventions
- **Primary keys:** UUID. The application generates `uuid4`, and `gen_random_uuid()` is the database default for raw SQL.
- **Timestamps:** `timestamptz` from the database clock (`now()`). Mutable tables have `created_at` and `updated_at`; `updated_at` is maintained by the ORM. Immutable tables (`agent_versions`, `task_dependencies`, `workflow_events`) have only `created_at`.
- **Status-like columns:** `VARCHAR` with a CHECK constraint generated from a Python `StrEnum`, not native PostgreSQL enums, which are costly to change. Values are UPPER_CASE, as in the handoff's task states.
- **Names:** a SQLAlchemy naming convention gives every constraint and index a deterministic name: `pk_<table>`, `fk_<table>_<cols>_<ref>`, `uq_<table>_<cols>`, `ck_<table>_<name>`, `ix_<table>_<cols>`. Names over PostgreSQL's 63-character limit are given explicitly.
- **JSON:** JSONB only for flexible, non-authoritative structure (settings, skills, configuration, payloads).
- **Migrations:** Alembic. They run synchronously with psycopg 3, so the Windows event-loop constraint from ADR-0005 doesn't apply. Each migration is reviewed by hand, and a test asserts the migrated schema matches the models with no drift.

### Workspace isolation at the database boundary
- Every workspace-owned table has `workspace_id NOT NULL`. A schema test enforces this, with an explicit, reasoned exemption list:
  - `users`: global identity.
  - `workspaces`: the tenant itself.
  - `agents`: NULL means a built-in agent.
  - `agent_versions`: scoped through `agent_id`.
  - `task_dependencies`: scoped through workflow-pinned keys.
- Child rows are pinned with **composite foreign keys** that include the scope:
  - `workflow_tasks (workspace_id, workflow_id)` references `workflows (workspace_id, id)`.
  - `workflow_events (workspace_id, workflow_id)` references `workflows (workspace_id, id)`.
  - `workflow_events (workflow_id, task_id)`, `task_dependencies (workflow_id, task_id | depends_on_task_id)` and `workflow_tasks (workflow_id, parent_task_id)` reference `workflow_tasks (workflow_id, id)`.
  - `agents (id, active_version_id)` references `agent_versions (agent_id, id)`.

  So the database itself rejects a task, event or dependency that points into another workspace or workflow, and an agent whose active version belongs to a different agent. The `workspace_id` copied onto child tables can't diverge from its parent, because the composite keys enforce it.
- Application rule, from Step 1.5 onward: every repository query takes `workspace_id` explicitly.
- PostgreSQL row-level security was considered. It's deferred until authentication (Step 1.5) provides a per-request workspace context to set.

### Deletion and retention
- **Deleting a workspace cascades** to its members, agents, workflows, tasks, dependencies, events and idempotency keys. Users are not workspace-owned and remain.
- **`RESTRICT`** applies to:
  - a user who owns a workspace;
  - a user who created a workflow or agent version;
  - an agent assigned to a task (agents are disabled, not deleted).
- **`workflow_events` is append-only:** a trigger rejects every `UPDATE`. `DELETE` stays possible for workspace deletion and future retention jobs. This guards against accidental rewrites; it is not legal immutability.
- `workflow_events.sequence` (an identity column) gives a total order even when timestamps are equal.
- Multi-task DAG cycles are rejected by the application's DAG validator (Step 1.7). The database rejects self-dependencies, duplicate edges and edges between workflows.

### Idempotency keys (OD-4)
- **Table `idempotency_keys`:** unique `(workspace_id, operation, idempotency_key)`. It holds `request_hash` (SHA-256 of canonical JSON), `status` (`PROCESSING` / `COMPLETED` / `FAILED`), `response_status_code`, the `response_body` (JSONB), `created_at` and `expires_at`. A CHECK requires a stored response once a record leaves `PROCESSING`.
- **Exactly-once execution:**
  1. `IdempotencyService.begin()` claims the key with `INSERT … ON CONFLICT DO NOTHING` **in the caller's transaction**.
  2. A concurrent duplicate blocks on the unique index until the first transaction ends.
  3. If the first transaction commits, the duplicate reads and replays the stored response.
  4. If it rolls back, the duplicate claims the key and runs the operation itself.
- **Transactional consistency:**
  - The business change and `complete()` / `fail()` commit in the same transaction, so a stored response never exists without its effects, and vice versa.
  - Unexpected errors roll everything back, including the claim, so a retry may run again.
  - `fail()` records expected 4xx outcomes, which are then replayed. 5xx outcomes must roll back.
- **Payload mismatch:** reusing a key with a different payload raises `IdempotencyKeyReusedError`, which becomes HTTP **409** `IDEMPOTENCY_KEY_REUSED`. A committed `PROCESSING` record (a caller bug) becomes 409 `IDEMPOTENCY_REQUEST_IN_PROGRESS`. A malformed key becomes 400 `INVALID_IDEMPOTENCY_KEY`. Valid keys are 1–255 visible ASCII characters.
- **No secrets cached:** before storing, the response body is scanned. Credential-like keys (password, secret, API key, authorization, cookie, credential, private key, and any key ending in "token") make the write fail with `UnsafeIdempotentResponseError`. Usage counters such as `input_tokens` are allowed.
- **Expiry and cleanup:**
  - Records expire after `IDEMPOTENCY_KEY_TTL_SECONDS` (default 86,400 = 24 h; allowed 60 s to 30 days).
  - An expired record is ignored and deleted when its key is presented again.
  - `IdempotencyService.purge_expired(batch_size)` deletes expired rows in batches, using `FOR UPDATE SKIP LOCKED` so it never blocks live requests.
  - Scheduling the purge periodically (Celery beat) arrives with background execution in Step 1.8. Until then, expired rows are removed lazily or by calling the purge.

## Alternatives considered
- **Two-phase idempotency** (commit `PROCESSING`, run the operation, then commit the result separately). It lets duplicates fail fast with "in progress", but it can leave stranded `PROCESSING` rows after a crash and needs lease timeouts. The single-transaction design is simpler and cannot strand rows. Its cost is that a concurrent duplicate waits for the first transaction, which is acceptable because API transactions are short; long LLM work runs asynchronously.
- **Redis-based idempotency.** Rejected: Redis is not authoritative state (ADR-0002), and it cannot commit atomically with PostgreSQL.
- **Native PostgreSQL enums.** Rejected: changing them needs special migration handling.

## Consequences
- Business services must call `begin()` and `complete()` / `fail()` within one transaction and commit once. The usage pattern is documented in `app/services/idempotency.py`.
- `WorkflowStatus` is provisional; Step 1.7 finalizes the workflow state machine with a migration.
- Adding an event type or status requires a migration that updates its CHECK constraint.
