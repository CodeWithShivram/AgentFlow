# Testing AgentFlow

How the test suites are organised, how to run them, and the rules every new test follows. The handoff's testing strategy (§15) is the source; this page maps it onto the repository.

## Suites at a glance

| Suite | Where | Needs | Run |
|---|---|---|---|
| Backend unit | `backend/tests/unit/` | nothing (settings are isolated from `.env`) | `uv run pytest -m "not integration"` |
| Backend integration | `backend/tests/integration/` (marker `integration`) | Docker: `scripts/infra.ps1 up` and the repo-root `.env` | `uv run pytest -m integration` |
| Frontend unit and component | `frontend/**/*.test.ts(x)` (Vitest, React Testing Library) | nothing | `npm test` |
| End-to-end journeys | `frontend/e2e/*.spec.ts` (Playwright) | Docker, plus the whole stack, which the run starts itself | `npm run test:e2e` |

Every gate in one command, from the repo root (add `-E2E` for Playwright):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1 -E2E
```

It runs, in order:
- **Backend:** ruff format, ruff check, mypy, `alembic check`, pytest.
- **Frontend:** lint, typecheck, Vitest, Playwright, build.

CI (GitHub Actions) runs everything except Playwright. Run the journeys locally before committing a step.

## Backend integration tests

- **Real services.** Tests run against real PostgreSQL 18 and Redis, never SQLite or fakes.
- **Database per session.** Each pytest session creates a disposable database, `agentflow_test_<random>`, migrates it to head and drops it at the end. The development database is never touched.
- **Roles.**
  - API tests connect as a restricted test role through the same row-level security as production.
  - Worker-side code runs through `worker_factory`, as the restricted worker role, so missing grants fail tests.
  - `admin_factory` (the administrator) is only for arranging data and reading results.
- **Missing infrastructure.** Without Docker, integration tests fail with a hint to run `scripts/infra.ps1 up`. If Docker Desktop has stopped, every test errors at setup: start Docker and rerun.
- **Migrations.** `test_migrations.py` upgrades and downgrades every revision one step at a time, so a new migration is covered automatically.

## Rules for new tests

- **No real models.** Pass `models=` with a `MockModelAdapter` (script replies with `.script(...)`), or run `MockAgent` explicitly. Never use the global gateway or a real key; LiteLLM tests use `mock_response`.
- **Pass worker-side dependencies explicitly.** Calls to `runner.run_agent` whose agent reaches a model or tool also pass `is_current=` (unit: `lambda: True`; integration: `runner.attempt_is_current` against the test database). The same goes for `record_tool_call=`. The defaults read the worker database from `.env`.
- **Isolate real-worker tests.** A test that starts a Celery worker uses its own Redis database and resets Celery's cached broker pools (`celery_app._pool`, `celery_app.amqp._producer_pool`) with `monkeypatch`. Otherwise it publishes through an earlier test's broker, and its worker waits forever.
- **Count outbox rows by type.** Every status change also writes a `workflow.orchestrate` row, so filter outbox counts by event type.
- **Verification is a stand-in until Phase 5.** Tasks reach COMPLETED only through the VERIFIER actor. Tests that need completed tasks move them SUBMITTED → VERIFYING → COMPLETED as VERIFIER, under the workflow lock.
- **Never weaken a test to make it pass.** If a test is wrong, explain why and get a review first. When a change alters behaviour on purpose, update the test and say so in `progress.md`.
- **Prove the test can fail.** Every step ends with a deliberate-bug check: break the behaviour, show the test failing, restore it byte-for-byte.

## Parallel and sequential execution

`backend/tests/integration/test_execution_scenarios.py` (Step 4.6) drives whole workflows from start to completion. Its tasks are pre-assigned, so the scheduler alone decides what runs when.

| Scenario | What it shows |
|---|---|
| Chain A → B → C | One task at a time, in dependency order |
| Diamond (A, then B and C, then D) | B and C run in the same round; D waits for both |
| Fan-out of 5, limit 2, mixed priorities | Rounds of two, highest priority first |
| Resume with ready and waiting tasks | One decision for the free slots, by priority; never more dispatches than slots |
| Five simultaneous claims, limit 2 | Exactly two claims succeed; three are deferred |
| 20 seeded random graphs (3–8 tasks, limit 1–3) | Late, shuffled deliveries and random finishing order. At every claim: never more than the limit running, and no task starting before its dependencies completed. Every task completes exactly once |
| Diamond through a real Celery worker (2 threads, Redis) | B's and C's attempts overlap in time; A finishes before them and D starts after both |

Related tests:
- **Scheduling, slots and priority:** `test_background_execution.py` (Steps 1.8, 4.4).
- **Recovery and cooperative cancellation:** same file (Step 4.5).
- **Duplicate delivery:** same file.

End to end, journey 12 (`frontend/e2e/workflows.spec.ts`) pre-assigns three tasks. After the start, the two independent ones run without anyone assigning them, and the task that needs both stays Pending. The sequential half of that journey needs Phase 5 verification, so it stays in backend tests for now.

A failing random-graph case prints its seed. Rerun it alone:

```powershell
uv run pytest "tests/integration/test_execution_scenarios.py::test_random_graphs_respect_the_limit_and_dependency_order[7]"
```

## End-to-end journeys

The README describes what a Playwright run starts and where its logs go. Each journey registers its own user through the API, so journeys don't share data. They assert what a person sees: roles, labels and visible text, not CSS classes.

## Known sources of noise on Windows

- **Vitest timeouts.** Under heavy machine load, React component tests can hit Vitest's 5-second timeout. The timed-out tests pass when rerun alone. Rerun before suspecting a regression, and never raise timeouts silently.
- **Docker.** Docker Desktop can stop after a restart or sleep. Then the integration suite errors at setup.
- **Time limits.** Celery's time limits are not enforced by the Windows `solo` pool, only by Linux prefork workers.
