# ADR-0010: Background execution, transactional outbox, worker database access and cleanup

- Status: Accepted
- Date: 2026-11-09
- Resolves: OD-8 (outbox scope), OD-9 (worker DB access), OD-12 (cleanup scheduling)
- Refines: ADR-0002 (outbox), ADR-0009 (single transition service)

## Context
Step 1.8 adds Celery workers. The owner decided:
- a minimal but reliable outbox now (OD-8), with advanced recovery in Phase 4;
- synchronous SQLAlchemy and psycopg 3 for workers, with the async API unchanged (OD-9);
- hourly cleanup through Celery Beat (OD-12).

There is no LLM execution yet; the worker runs a harmless mock agent.

## Decisions

### One transition engine for both runtimes
ADR-0009 requires one place that changes statuses, but workers are synchronous and the API is async.
- The engine moved to `app/workflows/engine.py` and works on a sync `Session`. Its behaviour is unchanged; all Step 1.7 tests pass on it unmodified.
- Workers call it directly.
- The API keeps the async functions in `app/services/transitions.py` as thin adapters. Each calls the engine through `AsyncSession.run_sync`, on the same connection, transaction and identity map.

### Transactional outbox (OD-8)
- **`outbox_events` columns:**
  - a stable `id` (also the broker message ID and the consumer idempotency key);
  - `event_type`; workspace, workflow and task IDs; a JSON `payload` of **identifiers only**;
  - `status` (`PENDING`, `PROCESSING`, `PUBLISHED`, `FAILED`), `attempts`, `available_at`;
  - the lease (`lease_owner`, `lease_expires_at`), `published_at` and `last_error`.
  - Partial indexes serve the relay's two scans and cleanup.
- **When the engine writes `task.dispatch`, always in the same transaction as the state change:**
  - a task enters `ASSIGNED` in a RUNNING workflow;
  - a workflow (re)enters `RUNNING`, for every `ASSIGNED` task;
  - a running task frees an execution slot, for every waiting `ASSIGNED` task.

  The API process never talks to the broker.
- **Relay** (`agentflow.relay_outbox`, a Beat task every 5 s) runs three bounded steps and never holds a transaction during the broker call:
  1. **Claim, in one transaction:** `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED LIMIT n)` takes pending rows that are due and processing rows whose lease expired. It sets `PROCESSING`, this relay's lease (`OUTBOX_LEASE_SECONDS`) and `attempts + 1`, then commits.
  2. **Publish, with no transaction:** `send_task` with `retry=False`; the message ID is the event ID.
  3. **Record, in one short transaction per row, guarded by `lease_owner`:**
     - success: `PUBLISHED` plus `published_at`;
     - failure: back to `PENDING` with exponential backoff (capped at 300 s) and a sanitized `last_error` (URL credentials removed), or `FAILED` after `OUTBOX_MAX_ATTEMPTS`.

     Events are never deleted on failure. A relay whose lease expired cannot overwrite the row a newer claim now owns.
- **Delivery is at-least-once, not exactly-once.** A crash between steps 2 and 3, a lost lease, or a Redis visibility-timeout redelivery can each produce a duplicate message.
- **Consumers deduplicate on the event ID:** `inbox_messages (event_id, consumer)` is inserted first, in the consumer's own transaction. The first committed insert wins; later deliveries stop there.
- With the Redis transport, "the broker confirmed" means the message write to Redis succeeded. Redis has no publisher confirms.

### Celery
- **Serialization:** JSON only (`task_serializer`, `result_serializer`, `accept_content=["json"]`). Results are ignored, because state lives in PostgreSQL.
- **Acknowledgement:** `task_acks_late=True` with `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, and a Redis `visibility_timeout` of 3,600 s, longer than the 660 s hard limit on executions.
- **Queues:**
  - `execution`: `agentflow.execute_task`;
  - `maintenance`: `agentflow.relay_outbox` and `agentflow.cleanup`.

  They are separate so housekeeping is never stuck behind agent work.
- **Broker** is the same Redis server on its own logical database (`CELERY_BROKER_REDIS_DB`, default 1). Its credentials come from `.env`.
- **Logging:** workers use the application's JSON logging with secret redaction. A test confirms the Redis password appears in no log.
- **Processes:** a worker and Beat are separate processes. Windows development uses `--pool=solo`; Linux production should run prefork workers (`--concurrency N`), per queue as needed, plus exactly one Beat process.

### Execution (mock agent)
`execute_task(event_id, ids…)` runs three bounded steps (`app/execution/runner.py`):
1. **Claim, in one transaction:**
   - record the event in the inbox; a duplicate stops here;
   - lock the workflow;
   - check that the task is still `ASSIGNED` to the dispatched agent version (otherwise stale), that the workflow is `RUNNING` (otherwise not running; resume re-dispatches), and that a `max_parallel_tasks` slot is free (otherwise deferred; a finishing task re-dispatches);
   - `ASSIGNED→QUEUED` (SCHEDULER) → `IN_PROGRESS` (WORKER);
   - insert an `agent_executions` row (attempt = `attempt_count + 1`, plus `dispatch_event_id`).
2. **Mock work,** outside any transaction.
3. **Finish, in one transaction** (retried up to 3 times on transient database errors):
   - success: `IN_PROGRESS→SUBMITTED` (WORKER). Agents never complete tasks; verification arrives in Phase 5.
   - failure or time-out: `REWORK_REQUIRED`, or `FAILED` on the 3rd attempt (handoff default of 3 attempts).
   - a task cancelled meanwhile: the execution is recorded as `CANCELLED`.

   Every attempt stays as its own `agent_executions` row (unique `(task_id, attempt_number)`).

The time limits are 600 s soft and 660 s hard (handoff default agent timeout). Celery enforces them only on pools that support signals (prefork on Linux); the Windows `solo` pool can't.

### Worker database access and roles (OD-9, coordinated with OD-13)
- **Separate sync engine:** `app/db/sync_session.py` creates its own psycopg 3 sync engine per process, discarding without closing any engine inherited by fork. `session_scope` rolls back on any exception and always closes.
- **Restricted role:** workers connect as **`agentflow_worker`**, which is `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION`, owns nothing, and holds exactly the per-table grants in `app/db/roles.py`. It has **no access to `users`** (password hashes). `python -m app.db.roles provision-worker` is idempotent and resets the role's grants to that list; re-run it after migrations.
- **Row locks need UPDATE privilege** in PostgreSQL. Tables the worker locks (`workflows`, `workflow_tasks`, `agent_executions`, `outbox_events`) have UPDATE. Cleanup tables deliberately don't, so cleanup does not use `FOR UPDATE` (see below).
- **Roles under OD-13:**
  - the migration owner (today the superuser `POSTGRES_USER`) becomes a non-superuser owner;
  - the API moves to an equivalent `agentflow_api` role.

  RLS can then be enabled for the API and worker roles without redesigning worker access: neither bypasses RLS, and the worker already sets no session state that RLS would need beyond the per-transaction context designed in ADR-0007. **RLS stays disabled** until the API stops using the superuser.

### Cleanup (OD-12)
- `agentflow.cleanup` runs **hourly** (Beat `crontab(minute=0)`) on the maintenance queue, as the worker role.
- Each category deletes in transactions of at most `CLEANUP_BATCH_SIZE` rows, up to `CLEANUP_MAX_BATCHES` per run, using indexed expiry columns (migration 0005 adds `refresh_tokens.expires_at` and `workspace_invitations.expires_at`).
- It logs counts and duration only.
- **Retention:**

  | Records | Deleted when |
  |---|---|
  | `idempotency_keys` | expired |
  | `refresh_tokens` | expired > 30 days ago |
  | `auth_sessions` | expired > 30 days ago; **sessions revoked for refresh-token reuse: > 90 days** |
  | `workspace_invitations`, never accepted | expired > 30 days ago |
  | `workspace_invitations`, accepted | **kept** (they record how membership was granted) |
  | `outbox_events`, `PUBLISHED` | published > 7 days ago (`FAILED` kept for Phase 4) |
  | `inbox_messages` | processed > 7 days ago (redelivery is impossible by then) |

- **Concurrency without row locks:** rows are deleted by key, without a locking subselect. Concurrent runs can't double-delete, because a second run waits on the first's row locks and then finds the rows gone. This avoids granting the worker UPDATE on these tables.

## Alternatives considered
- **Publishing to Redis inside the business transaction:** rejected; a commit/publish split loses or invents work.
- **Async workers:** rejected by OD-9.
- **A second, sync copy of the transition rules:** rejected, as it would break the single-source rule.
- **A dedicated relay daemon:** deferred. A 5-second Beat task is enough for the MVP; Phase 4 may add one for lower latency.
- **`FOR UPDATE SKIP LOCKED` in cleanup:** rejected, because it needs UPDATE privilege on security tables.

## Consequences and limitations
- Dispatch latency is up to the relay interval (5 s) plus queueing.
- **Stuck execution:** a worker that dies after claiming leaves the task `IN_PROGRESS` with a `RUNNING` execution. Redelivery stops at the inbox. Detecting and recovering stale executions (heartbeats or time-out sweeps) is Phase 4.
- `FAILED` outbox rows are kept but not yet surfaced or replayed (Phase 4).
- **No time limits on Windows `solo` workers.**
- Exactly one Beat process must run; Celery does not prevent two.
