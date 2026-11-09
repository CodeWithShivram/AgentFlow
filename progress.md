# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.7 — Workflow and task management
- Next step: 1.8 — Background execution foundation (await explicit go-ahead; needs OD-8, OD-9, OD-12)

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
| 1.7 | `feat(workflows): implement workflow and task management` | latest commit touching `backend/app/services/transitions.py` |

## Environment snapshot (2026-11-09)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`, nothing pushed |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Docker | 29.8.2, Compose v5.6.0. Docker Desktop was not running at session start; I started it and brought the `agentflow` stack up healthy |
| Dev database | `agentflow` at revision `0004 (head)`; six built-in agents seeded |
| Port 8000 | Used by another local project (`easa`); run AgentFlow smoke servers on another port |

## Current implementation state
- Steps 1.3–1.6: foundation, schema, idempotency, authentication and RBAC, agent registry.
- Migration `0004`:
  - the final 10 workflow statuses (OD-11), with an `autonomy_policy` CHECK (`approval_required` or `autonomous`);
  - `workflows.allow_dynamic_agent_proposals` and `workflows.revision`;
  - `workflow_tasks.required`, `removed_at` (CHECK: removed means CANCELLED) and `revision`;
  - six new event types.
- `app/workflows/state_machine.py`: the approved workflow and task matrices, with allowed actors per edge.
- `app/services/transitions.py`: the only place statuses change. It writes events atomically and runs reconciliation (readiness, BLOCKED/FAILED/COMPLETED propagation, cascade cancellation).
- `app/services/workflows.py`: idempotent create, list and get, user transitions (ADMIN to approve a plan or unblock), events.
- `app/services/tasks.py`:
  - task CRUD with plan-edit rules;
  - DAG dependencies with cycle detection;
  - assignment pinning the active agent version, with skill checks;
  - cancellation.
- `app/api/v1/workflows.py`: routes under `/api/v1/workspaces/{workspace_id}/workflows`.

## Checks last executed (Step 1.7)
- **Dev DB migrations:** `alembic upgrade` to 0004, `alembic check` clean, `downgrade -1` and re-upgrade ✔
- **`scripts/check.ps1` ✔:**
  - ruff format and check; mypy strict (92 files); alembic check;
  - pytest **689 passed** (519 unit, 170 integration);
  - eslint, typecheck, vitest 2, next build.
- **Transition coverage:** every ordered pair of statuses (100 workflow, 196 task) checked against an independent copy of the approved matrix, plus actor invariants.
- **Deliberate-bug checks** (each reverted afterwards), all caught:
  - cycle check off: 1 failure;
  - dependents becoming ready early: 2;
  - no completion guard: 1;
  - agents allowed to complete tasks: 1;
  - unscoped workflow lookup: 1;
  - plan approval without ADMIN: 1.

## Issues found and fixed during Step 1.7
- **The brief said the workflow status definitions were "in this conversation"; they weren't.** I proposed both transition matrices and the owner approved them before implementation.
- **Design flaw found before testing:** escalation could deadlock a BLOCKED workflow, because reassignment needed RUNNING. Now only dispatch (QUEUED/IN_PROGRESS) needs RUNNING.
- **Bug:** closing a workflow refused to cancel its leftover tasks. Cascade cancellation is now allowed only for that closing transition.
- **Bug:** `populate_existing` on task queries overwrote unflushed status changes in app sessions (`autoflush=False`). Removed it, transitions now flush, and the lifecycle tests use production-style sessions; the original tests used autoflush and missed this.

## Known issues / limitations
- A required task blocked by a cancelled dependency can only be resolved by cancelling the workflow, until replanning exists (OD-16, Phase 5).
- Tasks are moved through scheduler, worker and verifier transitions only by services and tests; those actors arrive in Step 1.8 and Phase 5. The API exposes user transitions only.
- Plan limits are 200 tasks per workflow and 50 dependencies per task.
- **Backlog (recorded in `IMPLEMENTATION_PLAN.md`):**
  - OD-15: database-level case-insensitive agent names;
  - OD-13: RLS before Step 1.10;
  - OD-14: trusted proxy IPs before hosted deployment;
  - OD-12: scheduled cleanup in Step 1.8.
- Frontend `budget_limit` types arrive with the frontend in Step 1.9.
- Still open: OD-10 (`httpx2` warning), the dev-only `minimatch` finding, and the Redis licence review.

## Blockers
None.

## Next exact action
On approval, with OD-8 (outbox scope), OD-9 (worker DB access) and OD-12 (cleanup scheduling) decided, start Step 1.8:
- Celery with Redis;
- a mock worker driving SCHEDULER and WORKER transitions through `transitions.py`;
- `agent_executions` records per attempt;
- outbox-based dispatch;
- bounded retry and timeout;
- duplicate-delivery tests.
