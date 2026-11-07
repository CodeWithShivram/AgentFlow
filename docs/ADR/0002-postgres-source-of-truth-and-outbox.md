# ADR-0002: PostgreSQL as source of truth; Redis as transport; transactional outbox

- Status: Accepted
- Date: 2026-11-07

## Context
Workflow and task state must survive worker restarts, duplicate queue delivery and partial failures. Writing to the database and publishing to a queue are not atomic.

## Decision
- PostgreSQL holds all authoritative business state (workflows, tasks, executions, events, approvals, usage).
- Redis is used only as the Celery broker/cache; it is never the sole record of task state.
- Task dispatch writes an outbox row in the same transaction as the state change; a relay publishes outbox rows to Celery. Workers claim executions idempotently (row lock or optimistic version check) so duplicate deliveries do not create duplicate accepted side effects.
- A minimal outbox is introduced in Step 1.8; hardening (relay robustness, dead-lettering) in Step 4.3 (see OD-8).

## Alternatives considered
- Publish to Celery after commit without an outbox: silently loses dispatches if the process dies between commit and publish.
- Redis as state store: no relational integrity, weaker durability guarantees.

## Consequences
- An extra table and relay process/loop to operate.
- Workers must be written to be idempotent from the start.
