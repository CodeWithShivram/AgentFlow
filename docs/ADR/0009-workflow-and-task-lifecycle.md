# ADR-0009: Workflow and task lifecycle, DAG rules and budget naming

- Status: Accepted
- Date: 2026-11-09
- Resolves: OD-5 (budget naming), OD-11 (workflow statuses)
- Supersedes: the `max_budget` field in the handoff's example request (§11), and the provisional workflow statuses of ADR-0006

## Context
Step 1.7 adds workflow and task management. The owner fixed the field name `budget_limit` and the ten workflow statuses. The definitions and transitions below were proposed during Step 1.7 and approved by the owner. Task statuses follow handoff §6, whose edges were also approved here. There is no LLM planning and no Celery execution yet; later steps drive the same transitions through the same service.

## Decisions

### Naming (OD-5)
`budget_limit` everywhere: database, API, services and documentation. `max_budget` is rejected (unknown field, 422), not aliased.

### Workflow statuses (OD-11)
| Status | Meaning | Allowed next statuses |
|---|---|---|
| PENDING | Created; no plan yet | PLANNING, CANCELLED, FAILED |
| PLANNING | Plan and task DAG being built | AWAITING_APPROVAL, READY¹, FAILED, CANCELLED |
| AWAITING_APPROVAL | Plan submitted; needs approval | READY² (approved), PLANNING (changes), CANCELLED, FAILED |
| READY | Plan approved; not started | RUNNING, PLANNING, CANCELLED |
| RUNNING | Tasks executing | PAUSED, BLOCKED, COMPLETED³, FAILED, CANCELLED |
| PAUSED | Paused by a user; no new dispatch | RUNNING, CANCELLED |
| BLOCKED | Needs intervention | RUNNING⁴, PAUSED, FAILED, CANCELLED |
| COMPLETED, FAILED, CANCELLED | Terminal | — |

1. Only when `autonomy_policy = autonomous`. With `approval_required` (the default) the plan must go through AWAITING_APPROVAL. Either way the plan needs at least one task.
2. A user decision that requires **ADMIN** (handoff §13: approval permission is distinct from execution permission).
3. Platform only, and only when every required, non-removed task is COMPLETED.
4. Requires **ADMIN**, and no required task may still be ESCALATED, BLOCKED or CANCELLED.

Users can never request COMPLETED, FAILED or BLOCKED. Those are platform outcomes.

### Task transitions (handoff §6)
| From | To |
|---|---|
| PENDING | READY (platform, all dependencies COMPLETED), BLOCKED, CANCELLED |
| READY | ASSIGNED, BLOCKED, CANCELLED |
| ASSIGNED | QUEUED, READY (unassign), CANCELLED |
| QUEUED | IN_PROGRESS, ASSIGNED (dispatch failed), CANCELLED |
| IN_PROGRESS | SUBMITTED, WAITING_APPROVAL, REWORK_REQUIRED, ESCALATED, FAILED, CANCELLED |
| SUBMITTED | VERIFYING, CANCELLED |
| VERIFYING | COMPLETED, REWORK_REQUIRED, WAITING_APPROVAL, ESCALATED, FAILED |
| REWORK_REQUIRED | ASSIGNED (retry), ESCALATED, FAILED, CANCELLED |
| WAITING_APPROVAL | QUEUED (approved), FAILED (rejected), CANCELLED |
| BLOCKED | READY, PENDING, FAILED, CANCELLED |
| ESCALATED | ASSIGNED (reassign), FAILED, CANCELLED |
| COMPLETED, FAILED, CANCELLED | — |

**Actors.** Every edge also names who may perform it:
- **USER:** an authenticated person.
- **SCHEDULER:** assigns and dispatches tasks.
- **WORKER:** the agent runtime.
- **VERIFIER:** platform verification.
- **SYSTEM:** readiness, propagation and policies.

Key rules:
- An agent (WORKER) can only start (`QUEUED→IN_PROGRESS`), submit (`IN_PROGRESS→SUBMITTED`) or request approval. **Only VERIFIER can reach COMPLETED.**
- Readiness (`PENDING→READY`) is computed by the platform; no actor can set it directly.
- The matrix is pure data in `app/workflows/state_machine.py`. A unit test checks every ordered pair of statuses (100 workflow, 196 task) against an independent copy of the approved matrix.

**Execution preconditions.**
- No transitions at all once the workflow is terminal.
- Task execution transitions only while the workflow is RUNNING, PAUSED or BLOCKED.
- Dispatch (`QUEUED`, `IN_PROGRESS`) only while RUNNING.
- Assignment is allowed while BLOCKED, so an escalated task can be reassigned and the workflow resumed.

### One transition service
- `app/services/transitions.py` is the only code that changes a status. It checks the matrix and actor, applies data guards (dependencies, required tasks, autonomy), sets timestamps, increments `revision`, and **writes the event through the same session**, flushing so state and history commit or roll back together.
- **`reconcile_workflow` keeps the workflow consistent with its tasks** after every task change and on (re)start:

  | Situation | Result |
  |---|---|
  | A required task FAILED | Workflow FAILED; other tasks cancelled |
  | A required task ESCALATED, BLOCKED, or CANCELLED mid-run | Workflow BLOCKED |
  | Every required task COMPLETED | Workflow COMPLETED; unfinished optional tasks cancelled |
  | Otherwise | PENDING tasks whose dependencies all COMPLETED become READY; PENDING tasks depending on a FAILED or CANCELLED task become BLOCKED |

- Cancelling or failing a workflow cancels every unfinished task. Cancellation is persisted; cooperative stop of running work arrives with the executor (Step 1.8).

### Plans and the DAG
- Tasks and dependencies can be edited only in PENDING, PLANNING, AWAITING_APPROVAL and READY. **Editing in AWAITING_APPROVAL or READY returns the workflow to PLANNING**, so an approved plan can never change silently.
- Dependencies are `(task, depends_on)` edges within one workflow. The database's composite foreign keys already make cross-workflow and cross-workspace edges impossible. The service also rejects:
  - self-dependencies;
  - unknown, removed or foreign tasks (all with one 422 `INVALID_DEPENDENCY`, so nothing leaks);
  - duplicates (409);
  - **cycles** (422 `DEPENDENCY_CYCLE`): a new edge `t → d` is refused if `d` already reaches `t`, checked by depth-first search over the workflow's edges.
- Because every edit holds the workflow row lock, two concurrent opposite edges cannot both pass the check. A test confirms this.
- **Limits:** 200 tasks per workflow, 50 dependencies per task.
- **Removing a task keeps the row:** events reference it and are append-only. The task becomes CANCELLED with `removed_at` set (enforced by a CHECK), and its dependency edges are deleted. Removed tasks never count toward completion.
- Tasks are `required` by default. Optional tasks don't block completion.

### Concurrency
- **Pessimistic:** every mutation locks the workflow row (`SELECT … FOR UPDATE`) first, so plan edits, dependency checks and transitions of one workflow are serialized.
- **Optimistic:** workflows and tasks carry a `revision`. User transitions and task edits must send the revision they read; a mismatch returns 409 `WORKFLOW_REVISION_CONFLICT` / `TASK_REVISION_CONFLICT` with the current value.

### Assignment and history
- Assignment resolves the agent's **active published version** (`resolve_runnable_version`, ADR-0008), checks that the agent's capabilities cover the task's `required_skills` (422 `AGENT_LACKS_SKILLS`), and pins `assigned_agent_version_id`.
- A retry (`REWORK_REQUIRED → ASSIGNED`) increments `attempt_count`. Every assignment and reassignment event records the previous agent and version.
- Execution and submission records arrive in Step 1.8 as new `agent_executions` rows per attempt. Past attempts are never overwritten.

### Events
Every change appends a `workflow_events` row in the same transaction:
- the handoff type where one exists, e.g. `TASK_ASSIGNED`, `AGENT_SUBMITTED`, `VERIFICATION_FAILED`, `APPROVAL_REQUESTED`/`RESOLVED`, `PLAN_CREATED`, `WORKFLOW_*`;
- otherwise one of the types added in migration 0004: `WORKFLOW_STATUS_CHANGED`, `TASK_STATUS_CHANGED`, `TASK_UPDATED`, `TASK_REMOVED`, `DEPENDENCY_ADDED`, `DEPENDENCY_REMOVED`.

Status payloads are `{from, to, actor, reason?}`. **Prompts are never copied into events** (handoff §5.8).

### Creation, idempotency and budget
- `POST …/workflows` accepts an optional `Idempotency-Key` and uses `IdempotencyService` (ADR-0006). The fingerprint includes the user, so another member reusing a key gets a conflict.
  - Retries replay the stored 201 with `Idempotent-Replayed: true`.
  - A different payload returns 409 `IDEMPOTENCY_KEY_REUSED`.
  - Concurrent duplicates create exactly one workflow.
- **Budget:** `budget_limit` is a decimal from 0 to 1,000,000 with at most 4 decimal places, and NaN or infinity are rejected (NUMERIC(12,4)). It's serialized as a string to avoid float rounding. `currency` is 3 letters, normalized to upper case.
- `autonomy_policy` is `approval_required` (default) or `autonomous`, enforced by a CHECK. `allow_dynamic_agent_proposals` is stored for the planner (Phase 3).

## Consequences
- Each change costs a few queries under a row lock. That's fine for plans of up to 200 tasks.
- **Recovery is limited until Phase 5:** a required task blocked by a cancelled dependency can only be resolved by cancelling the workflow, or by replanning once that exists.
- Downgrading migration 0004 deletes events of the new types, since UPDATE is forbidden on events, and maps new statuses to the nearest old ones.
