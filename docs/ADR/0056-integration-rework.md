# ADR-0056: Integration rework through fix tasks

- Status: Accepted
- Date: 2026-11-27
- Implements: Step 9.9; playbook §2E ("send targeted rework tasks to the responsible agent when verification fails; bound retries; escalate")
- Amends: ADR-0051, which deferred automatic targeted rework
- Found by: the first live Gemini project. The gate correctly refused a generated test (`assert 19 == 18`), but the project then needed a person to fix it.

## Context
- **What happens today.** When a software project's integration checks fail, the project is BLOCKED until a person resumes or replans it.
- **What's needed.** Most failures are ordinary code or test mistakes that the agent that wrote the files can fix, given the failing output.
- **What stays.** Completed tasks are terminal (history and the acceptance trigger, ADR-0034), and must stay that way.

## Decision
### When
- **Fixes are requested** when an integration run fails with `CHECKS_FAILED` and fewer than `MAX_FIX_ROUNDS` (2) failed runs have already asked for fixes.
- **Otherwise the project blocks,** as before. That covers a sandbox that could not run (`NOT_RUN`) and a run past the round limit.

### Who
- **Owners of named files.** The responsible tasks are the current owners of the tracked files that the failing output names. The current owner of a path is the newest completed task whose scope covers it, so a later fix task supersedes the original.
- **Fallback.** When no file is named, every current owner (one per scope) is asked.

### How (`app/projects/integration_rework.py`)
- **A fix task per responsible task.** Instead of reopening completed tasks, the platform adds a **fix task** for each one (actor SYSTEM, `TASK_CREATED` with `reason: integration rework`). It has:
  - the same file scope, skills, deliverables and agent version;
  - the original criteria plus `integration` ("with these files, the project's integration checks pass");
  - `required = true`;
  - `fixes_task_id` (the original planned task) and `fixes_integration_id` (the failed run) set. Migration 0035 adds both, paired by a check constraint.
- **Scheduling.** The workflow stays RUNNING and the scheduler dispatches the fix tasks.
- **Context.** The contract (v5) of a fix task adds:
  - `integration_feedback`: the failed checks' output, already capped and redacted by the sandbox, capped again at 12,000 characters;
  - `current_files`: the scope's committed files, read with the workspace's path checks (≤ 40 files, ≤ 120,000 characters).

  Both reach the model only inside `<task_contract>`, as data, with a rule to propose the complete corrected files of the scope.
- **What happens next.** Fix tasks are verified like any task (`deterministic/v3` and semantic evaluation). Once accepted and applied, their files replace the scope (the newest commit wins), and the gate runs again.
- **Workers** may now INSERT into `workflow_tasks`.

### Lessons from the tempconv run (2026-11-27)
- **Context.** A fix task also gets the rest of the project as read-only `dependency_files` (it has no planned dependencies). Without them, a tests fix could not see that `cli.py` used argparse while the tests assumed click, and Gemini returned the test file unchanged.
- **No-op fixes are refused.** `deterministic/v4` adds `fix_changes`: a fix whose in-scope files equal the previous owner's accepted files fails with `FIX_UNCHANGED`. The task is retried with a fixed corrective sentence instead of spending an integration round on an identical commit.
- **Fresh rounds after a resume.** Rounds count only failed runs since a person last resumed the project from BLOCKED, so a resume gives the project new rounds.
- **Titles.** Fix tasks are titled after the original planned task (no "Fix integration: Fix integration: ...").

### Lost runs
- **The recovery sweep** (Celery Beat, every 60 s) closes integration runs still RUNNING after `integration_job.STALE_AFTER` (30 min, beyond the job's hard limit) as NOT_RUN `WORKER_LOST`, and requests a new run while the project is RUNNING.
- **Why.** In the live run, the gate crashed after claiming its run (the worker had not been re-provisioned with the new grant), and the project waited at "Integrating" with nothing to wake it.

## Consequences
- **Typical failures fix themselves.** A wrong expected value or a missing import is fixed without people, within at most two extra rounds.
- **Progress stays honest.** Fix tasks add their effort, so progress can step back while fixes run.
- **People still decide on persistent failures:** a blocked project, then resume, replan or a PARTIAL package.
- **Attribution is evidence-based but coarse.** A failing test may name only the test file while the bug sits in the code; the next round, or the fallback, then reaches the code's owner.
