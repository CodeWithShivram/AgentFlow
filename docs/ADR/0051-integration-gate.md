# ADR-0051: Project integration gate

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.5; ADR-0046 decision 5; playbook §8 (integration sequence) and §16 (COMPLETED only with evidence)
- Builds on: ADR-0034 (platform acceptance), ADR-0049 (workspaces), ADR-0050 (sandbox)

## Context
- **What verification already does.** Each task is accepted on its own (deterministic checks plus semantic evaluation), and accepted files land in the workspace (9.3).
- **What it doesn't do.** A project is only usable if the composed tree works: it compiles or type-checks, and its tests pass. Today a software project would complete as soon as its tasks did.

## Decisions

### Checks per stack profile
`StackProfile.checks` lists fixed platform commands, run in the profile's sandbox image:
- **`python-3.12`:**
  - `compile` (`python -m compileall -q .`);
  - `tests` (`python -m pytest -q -p no:cacheprovider`).
- **`node-24-typescript`:**
  - `typecheck` (`tsc -p . --noEmit`);
  - `tests` (`vitest run`).

  Both link the image's preinstalled `node_modules` into the throwaway copy first.

The checks never come from the project or a model.

### When the gate runs
- **For software projects, the engine no longer completes the workflow** when all required tasks are COMPLETED. Instead it requests `project.integrate`.
- **The last apply requests it too.** The apply job requests it after applying the last completed task, because the engine's request can arrive before the files are in the workspace.
- **The job runs only when:**
  - the workflow is a RUNNING software project;
  - every required task is COMPLETED and every completed task's latest successful attempt is APPLIED;
  - no integration is already RUNNING.

  Otherwise it consumes the event and does nothing (`waiting`, `running`, `stale`). Duplicate requests are therefore harmless.

### How it runs (`app/projects/integration_job.py`)
- **claim** (one transaction): records a RUNNING `project_integrations` row for the workspace's current commit.
- **execute** (no transaction): runs every check, keeping exit code, timeout, duration and capped, redacted output.
- **finish** (one transaction): stores PASSED, FAILED (`CHECKS_FAILED`) or NOT_RUN (the sandbox's code, e.g. `DOCKER_UNAVAILABLE`), once; a trigger allows only that single change. Then:
  - **PASSED:** if the workflow is still RUNNING at the same commit, it becomes COMPLETED;
  - **anything else:** it becomes BLOCKED, with the code in the event reason.

  A newer commit means newer work: the row is kept, and the next request checks the new tree.

### Completion needs evidence
- **Engine guard.** `transition_workflow` refuses COMPLETED for a software project unless its latest finished integration is PASSED (`INTEGRATION_REQUIRED`). Tests and any future code path are held to the same rule.

### After a failure
- **Retry.** People resume the BLOCKED workflow (BLOCKED → RUNNING). Reconciliation then requests a new integration on the current tree.
- **Fix.** They can also replan the remaining work (ADR-0033), for example adding a fix task, with the evidence from the failed checks.
- **Not yet automatic.** Targeted automatic rework of the task responsible is not attempted. Test failures cannot be attributed to one task's files reliably enough. This is tracked for Step 9.8 or later.

### Records and access
- **`project_integrations`** (migration 0033): workspace-scoped with RLS. Workers SELECT, INSERT and UPDATE (one finishing update; trigger).
- **API (VIEWER):** `GET …/workflows/{id}/integrations` lists runs, newest first, with check outcomes and output as plain text. `GET …/workflows/{id}/workspace-applications` lists what was applied.

## Consequences
- **What COMPLETED now means.** A software project is COMPLETED only after its checks ran and passed on the exact composed tree, and that tree is the one packaging (9.6) will read.
- **Without Docker** projects stop at BLOCKED with `NOT_RUN`, never COMPLETED.
- **Stranded runs.** A worker lost during `execute` leaves a RUNNING row. The next claim closes a RUNNING row older than 30 minutes as NOT_RUN (`WORKER_LOST`), longer than the job's 960 s hard limit, and starts a new run. Resuming the blocked workflow triggers that claim.
