# ADR-0004: Running Celery workers on a Windows development host

- Status: Proposed (finalise in Step 1.8)
- Date: 2026-11-07

## Context
Celery's default prefork pool does not officially support Windows. The developer uses Windows 11; Docker Desktop is installed.

## Options
1. Run the worker on the host with `--pool=solo` (or `threads`) for development.
2. Run the worker in a Linux container via Docker Compose (prefork pool, matches future deployment).

## Proposed decision
Support option 1 for fast local iteration and tests, and add option 2 as the reference way to run the worker in Step 1.8/1.10. Concurrency behaviour is validated against the containerised worker.

## Consequences
- Parallelism tests must not rely on host `solo` pool semantics.
- Celery config must not assume a specific pool.
