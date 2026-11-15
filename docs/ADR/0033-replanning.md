# ADR-0033: Replanning the remaining work of a paused or blocked workflow

- Status: Accepted
- Date: 2026-11-15
- Implements: Phase 5, Step 5.4 (replan with DAG validation); handoff §6 ("Replanning must preserve completed task history and require validation of the revised DAG") and §8 outcome REPLAN; resolves OD-16
- Builds on: ADR-0009 (workflow lifecycle), ADR-0014 and ADR-0015 (planning jobs and drafts), ADR-0016 (DAG validation), ADR-0017 (plan confirmation)
- Owner decisions (2026-11-15):
  - only a paused or blocked workflow is replanned, once no task is in flight; completed tasks are kept and unfinished ones replaced;
  - the model drafts the replan, and people confirm it;
  - the platform only suggests replanning when a workflow is blocked;
  - confirming follows the autonomy policy: an admin for approval-required workflows, a member otherwise.

## Context
- **The plan freezes once work starts.** Confirming a draft refuses a running workflow (`PLAN_NOT_EDITABLE`).
- **OD-16.** A required task that was cancelled, or a dependency chain broken by one, left a workflow BLOCKED. The only way out was to cancel the whole workflow.

## Decisions

### Requesting a replan
- **The endpoint.** `POST …/workflows/{id}/replan` (Member) is allowed when the workflow is PAUSED or BLOCKED, its analysis is ready, and no plan draft is being generated. Otherwise it returns 409 `REPLAN_NOT_ALLOWED`.
- **The job.** It requests a plan as before: the same outbox event and the same job (`app/planning/plan.py`). The job sees the workflow's status and drafts a **replan**:
  - the completed tasks get platform keys (`done-1`, `done-2`, …) and are listed as data with their titles and acceptance criteria;
  - the unfinished tasks are listed with their statuses;
  - agent output is never included;
  - the prompt (`replan/v1`) asks for the remaining work only.
- **Building on completed work.** A task may list completed tasks it builds on in `depends_on_completed`, a new optional field of planned tasks. A reply referring to unknown keys is invalid output, and is retried.
- **Storage.** The draft is stored as `kind = "replan"`, with its **basis** (`workflow_plans.basis`: the completed tasks' keys, ids and titles; migration `0022`). Nothing else changes until a person confirms it.

### Validation
- **The checks.** `analyse_plan` takes the draft's completed keys. A reference to anything else is an error (`UNKNOWN_COMPLETED_TASK`), and any such reference in a plain plan is too.
- **Where they run.** At preview and again at confirmation, together with the existing cycle, skill and parallelism checks.

### Confirming a replan
- **The endpoint is the same** confirm endpoint, with the workflow's `revision` and acknowledged warnings, under the workflow lock.
- **Refusals:**

| Situation | Response |
|---|---|
| Not PAUSED or BLOCKED | 409 `REPLAN_NOT_ALLOWED` |
| Approval-required workflow, confirmed by a non-admin | 403 `INSUFFICIENT_ROLE` (confirming is the approval) |
| A task QUEUED, IN_PROGRESS, SUBMITTED or VERIFYING | 409 `TASKS_IN_FLIGHT` |
| A basis task is no longer an active COMPLETED task | 409 `PLAN_DRAFT_STALE` |
| Errors in the revised graph, or unacknowledged warnings | as for plans (ADR-0017) |

- **What a replan changes:**
  - completed tasks are untouched;
  - every other unfinished task is removed from the plan as CANCELLED history, as manual removal does (already CANCELLED ones are only marked removed);
  - FAILED tasks stay as they are: a failure is final history, a removed task must be CANCELLED, and only optional tasks can be FAILED in a running workflow;
  - the draft's tasks are created PENDING with their dependencies, including edges to the completed tasks they build on;
  - `PLAN_CONFIRMED` records `kind: "replan"` and the created, removed and kept counts.
- **No resume.** Confirming never resumes work. The workflow stays PAUSED or BLOCKED until someone resumes it. With the blocking tasks replaced, unblocking passes its check, and the new tasks become ready as their dependencies allow.

### Suggesting it
- **Graph v4** (`orchestration/v4`). A BLOCKED workflow gets the step `unblock` ("The workflow is blocked by N required tasks: reassign or retry them, or replan the remaining work."), listing the blocking tasks. The PAUSED step mentions replanning too.
- **No automatic drafts.** The platform never requests a replan by itself.

### UI
- **Plan draft tab.** On a paused or blocked workflow, members get "Replan remaining work". A replan draft is badged "Replan" and lists the completed work it keeps, and each new task shows what completed work it builds on.
- **Confirming.** The confirm dialog states what is replaced and kept, without the replace/add choice. It is offered only to those allowed to confirm.

## Consequences
- **OD-16 is resolved.** A blocked workflow can continue without being cancelled, and the history of what was tried stays visible.
- **In-flight work.** A replan waits for in-flight work to finish (pausing lets it finish). A stuck attempt is recovered by the sweep (ADR-0029).
- **Cost.** Replanning costs one planning model call per request.
