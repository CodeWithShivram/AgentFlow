# ADR-0027: Outbox hardening, failed jobs and worker health

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 4, Step 4.3 (Celery dispatch and worker lifecycle: outbox hardening, idempotent claims); resolves OD-18; handoff §13 (dead-letter handling, health/readiness checks)
- Builds on: ADR-0010 (outbox, relay, workers), ADR-0012 (RLS)
- Owner decisions (2026-11-14):
  - failed jobs show on the workflow page, and admins can retry them;
  - stranded executions (OD-17) wait for Step 4.5;
  - readiness reports the relay heartbeat and backlog.

## Context
- **Already in place (ADR-0010):** leased relay claims, backoff, at-least-once delivery with inbox deduplication (idempotent claims), late acknowledgement and separate queues.
- **Missing:**
  - events the relay gave up on (`FAILED`) were kept but invisible, and could not be retried (OD-18);
  - nothing showed whether workers ran;
  - an unroutable event was retried 10 times for nothing.

## Decisions

### Permanent publish failures
- **The error.** `outbox.PermanentPublishError` marks failures that can never succeed. `publish_event` raises it when an event type has no route, or when a payload lacks the identifiers its task needs (`REQUIRED_PAYLOAD`).
- **The relay's response.** It records such an event as `FAILED` after the first attempt, with no backoff. Transient errors (the broker unreachable) keep the existing backoff and attempt limit.

### Failed jobs (OD-18)
- **Visibility.** `GET …/workflows/{id}/failed-jobs` (Viewer) lists the workflow's FAILED events, newest first, up to 100. `GET /workspaces/{id}/failed-jobs` (ADMIN, paginated) lists the workspace's.
- **What clients see.** Responses carry identifiers, the event type, the related task, attempts, times and the **error type only**: the exception class parsed from the relay's `last_error`. The stored error text is never returned, because it can name internal hosts.
- **Retry.** `POST …/failed-jobs/{event_id}/retry` (ADMIN) runs under the workflow lock:
  1. it puts a FAILED event back to PENDING with zero attempts, due now, with no lease;
  2. it writes a `BACKGROUND_JOB_RETRIED` workflow event (actor, user, event id and type, previous attempts) in the same transaction (migration `0017` extends the event-type check);
  3. retrying anything not FAILED returns 404 `FAILED_JOB_NOT_FOUND`.

  Requeuing is safe. Consumers deduplicate on the event id, and stale events (a task that moved on, a superseded analysis or plan request) change nothing when delivered.
- **UI.** The workflow page shows "N background jobs failed" with each job's purpose (run task, analyse request, draft plan, assess next step), task, attempts, error type and time. Admins get a Retry button.

### Worker heartbeat and readiness
- **The heartbeat.** After each relay run (Beat, every 5 s), the worker writes `agentflow:workers:relay_heartbeat` to Redis (app database, TTL 300 s): the run time, and how long the oldest *due* PENDING event has waited.
- **Why the worker measures the backlog.** The API cannot see outbox rows without a user, because of row-level security.
- **Failures.** A Redis or database failure while recording is logged, and never stops the relay.
- **The readiness check.** `/health/ready` gains a `workers` check:
  - `ok` when the heartbeat is under 60 s old and the oldest due event has waited at most 300 s;
  - `unavailable` without a recent heartbeat;
  - `lagging` with a backlog.

  It reports status only, with no counts or hosts on the unauthenticated endpoint. As before, the API is ready only when every check is `ok`.

## Consequences
- **Readiness depends on workers.** A stopped worker or Beat now marks the API not ready within about 60 s, so deployments must run them. The E2E stack starts them with the API, and readiness follows within one relay interval.
- **Retention unchanged.** Failed events remain until an admin retries them; cleanup never deletes FAILED rows (ADR-0010).
- **Still open: stranded executions (OD-17).** A worker crash after a claim leaves the task IN_PROGRESS. That remains for Step 4.5's restart recovery.
