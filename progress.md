# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.8 — Reliable background execution with Celery
- Next step: 1.9 — Frontend foundation (await explicit go-ahead; needs OD-6). OD-13 (RLS) is owner-scheduled before 1.10.

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | `9543279` |
| 1.5 | `feat(auth): add authentication and workspace RBAC` | `7eded85` |
| 1.6 | `feat(agents): implement agent registry and versioning` | `baaedb9` |
| 1.7 | `feat(workflows): implement workflow and task management` | `82483e6` |
| 1.8 | `feat(execution): add reliable background execution with Celery` | latest commit touching `backend/app/execution/outbox.py` |

## Environment snapshot (2026-11-09)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`, nothing pushed |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.8.2, Compose v5.6.0 — `agentflow` stack healthy |
| Dev database | `agentflow` at `0005 (head)`; built-ins seeded; `agentflow_worker` role provisioned |
| Local `.env` | now also holds a generated `WORKER_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |

## Current implementation state
- **Transition engine:** `app/workflows/engine.py` (sync) is the single transition engine. `app/services/transitions.py` is its async adapter, via `run_sync`. The engine writes `task.dispatch` outbox rows in the same transaction when a task becomes ASSIGNED, when a workflow resumes, and when a running task frees a slot.
- **Migration `0005`:**
  - `outbox_events` (lease, attempts, backoff, partial indexes);
  - `inbox_messages`;
  - `agent_executions` (composite FKs, unique `(task_id, attempt_number)`, unique `dispatch_event_id`);
  - expiry indexes on `refresh_tokens` and `workspace_invitations`.
- **`app/execution/`:**
  - `outbox.py`: enqueue; claim with SKIP LOCKED and leases; lease-guarded mark published/failed; inbox.
  - `relay.py`: claim, publish with no transaction, record.
  - `runner.py`: claim, mock agent, finish; at most 3 attempts.
  - `cleanup.py`: bounded hourly cleanup with retention rules.
- **`app/worker/`:** `celery_app.py` (JSON only, late acks, prefetch 1, execution and maintenance queues, Beat schedule) and `tasks.py` (relay, execute_task, cleanup).
- **`app/db/`:** `sync_session.py` (a per-process sync engine; `session_scope` always rolls back on error and closes) and `roles.py` (`agentflow_worker` grants and the provisioning command).

## Checks last executed (Step 1.8)
- **Migrations:** `alembic upgrade` to 0005, `alembic check` clean, `downgrade -1` and re-upgrade on the dev DB ✔
- **`scripts/check.ps1` ✔:**
  - ruff; mypy strict (107 files); alembic check;
  - pytest **724 passed** (529 unit, 195 integration);
  - eslint, typecheck, vitest 2, next build.
- **New integration tests,** against real PostgreSQL, as the worker role:
  - outbox commit and rollback with the state change;
  - 3 concurrent relays with no double claim;
  - lease expiry reclaim with the stale-owner guard;
  - publish with no open transaction;
  - broker failure keeping the event, backoff, then FAILED;
  - duplicate delivery from 4 concurrent consumers executing once;
  - stale dispatch ignored;
  - bounded attempts with kept history;
  - parallel-limit deferral and re-dispatch;
  - paused workflow with re-dispatch on resume;
  - cancellation mid-run;
  - session rollback and close;
  - role restrictions and an exact grant list;
  - cleanup retention, boundedness, idempotency and concurrency;
  - **an end-to-end run through real Redis and a real Celery worker.**
- **Deliberate-bug checks** (each reverted afterwards), all caught:
  - inbox deduplication off: 1 failure;
  - no lease guard: 1;
  - no dispatch on assignment: 5;
  - immediate give-up on publish failure: 1;
  - parallel limit ignored: 1;
  - no security retention: 2.
- **Startup, each independently,** with processes stopped by their own process tree:
  - API on :8010 reported ready;
  - the worker (solo pool) reported ready and executed relay tasks;
  - Beat sent `relay-outbox` every 5 s;
  - the Redis password appeared in no log, and no processes were left.

## Deviations and findings
- **Refactor of Step 1.7:** the transition logic moved from async to a sync engine with an async adapter, so workers can use the single transition service. Behaviour is unchanged; all Step 1.7 tests pass unmodified.
- **Row locks need UPDATE privilege.** The real-database tests showed this. Cleanup therefore deletes by key without `FOR UPDATE SKIP LOCKED`, so the worker role doesn't get UPDATE on security tables. It remains safe concurrently (tested).
- **Additions beyond the brief:** cleanup also prunes PUBLISHED outbox rows and inbox entries older than 7 days. FAILED outbox rows are kept.
- **Typing:** mypy relaxations apply only to Celery (untyped) and `app/worker/*`; `app/execution` stays strict.

## Known issues / limitations
- **OD-13:** RLS is not enabled. The API and migrations still use the superuser; the worker role is already compatible.
- **OD-14:** trusted proxy IP handling, before hosted deployment.
- **OD-17:** a worker crash after claim leaves the task IN_PROGRESS; redelivery is deduplicated, so recovery is Phase 4.
- **OD-18:** FAILED outbox rows are kept but not surfaced or replayable yet (Phase 4).
- **OD-15:** database-level case-insensitive agent names. **OD-16:** replanning for tasks blocked by a cancelled dependency.
- Celery time limits are not enforced by the Windows `solo` pool, only by Linux prefork. Exactly one Beat must run.
- Dispatch latency is up to 5 s (the relay interval).
- Worker logs report `service: agentflow-api` (same SERVICE_NAME); a distinct worker service name can be configured.
- Still open: OD-10 (`httpx2` warning), the dev-only `minimatch` finding, and the Redis licence review.

## Blockers
None.

## Next exact action
On approval, with OD-6 (frontend component system) decided, start Step 1.9:
- auth screens with cookie and CSRF handling, and single-flight refresh;
- dashboard shell;
- workflow list/create/detail using `budget_limit`;
- agent registry screen;
- a typed API client;
- tests.
