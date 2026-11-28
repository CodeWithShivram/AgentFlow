# ADR-0059: Change projects — follow-up changes to a completed project

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.12; picked as the most valuable next feature (the owner said "go with the best feature you think")
- Builds on: ADR-0048 (project plans), ADR-0049 (workspaces), ADR-0051 (integration gate), ADR-0052 (packages), ADR-0056 (fix context)

## Context
- **Projects are one-shot today.** A finished project cannot be extended. Asking for "add a checkout page" means a new project that rebuilds everything from scratch, with a new plan, new code and new risk.
- **Completed workflows are terminal.** COMPLETED is terminal (ADR-0009), acceptance is frozen (ADR-0034), and a VERIFIED package belongs to one commit (ADR-0052). Reopening a completed project would weaken all three.

## Decision
### A change is a new project linked to its base
- `POST …/workflows/{base_id}/changes {prompt}` (MEMBER, optional `Idempotency-Key`) creates a PENDING `SOFTWARE_PROJECT`. It is allowed only for a base that is COMPLETED, not deleted, has a project spec and whose latest integration PASSED. Otherwise the API returns 409 `CHANGE_NOT_AVAILABLE`, or 404 for a deleted or unknown base.
- **Columns** (migration 0038): `base_workflow_id` (same-workspace composite FK) and `base_head_sha` (the passed integration's commit), always set together and only on software projects (check constraints).
- **What it keeps from the base:** autonomy policy, budget, currency, parallelism, proposals setting and `project_spec`. Keeping `project_spec` means the planner sees only the base's stack profile and a profile change is refused (`PLAN_PROFILE_CHANGED`).
- **The base is never modified.** Its workflow, workspace and packages stay as they were.

### The workspace starts from the verified code
- `app/projects/changes.seed_change_workspace` copies the base's committed files into the new repository in one platform commit ("Start from the verified project at <sha>"), with every path check of `apply`. It is idempotent: a repository that already tracks files is left alone.
- **It refuses to guess.** `BASE_UNAVAILABLE` (no base repository), `BASE_CHANGED` (the base's HEAD is no longer `base_head_sha`) and `BASE_TOO_LARGE` (over packaging limits) fail the plan request (`PLAN_GENERATION_FAILED` with that code), and people can retry.
- **Who seeds:** the plan job seeds before the first plan, and the apply job seeds too if the workspace is not seeded yet. This amends ADR-0049's rule that only the apply job writes workspaces.

### Planning only the change
- **What the planner gets.** The prompt adds `<current_project>`: name, profile, architecture, every committed path, and file contents up to 60 files and 60,000 characters (`files_left_out` lists the rest). The rules (`software_change/v1`) are:
  - keep the name and profile;
  - plan tasks only for files to add, change or delete;
  - files outside every scope stay as they are;
  - a scope is replaced by exactly the proposed files, so prefer exact paths for existing files;
  - shared files already exist.
- **Shared files.** Like a replan, a change plan need not own the profile's shared files: the plan job (`PlanJob.change`) and confirmation (`SHARED_FILE_UNOWNED` is skipped when `base_workflow_id` is set) both accept that.

### Building the change
- **Task context.** Every task of a change project gets contract v5's `current_files` (its scope's committed files) and, after its planned dependencies' files, the rest of the project as `dependency_files`. Both come through `context.fix_context`, without integration feedback. The contract adds a fixed `change_rule`: the scope is replaced by exactly the proposed files, so keep every file to keep.
- **Everything else is unchanged.** Verification, apply, the integration gate on the whole tree (including UI checks for web projects), automatic fix rounds, packaging and progress work exactly as for new projects. The new VERIFIED ZIP contains the whole changed project.

### UI
- **On a completed project** (members): a "Request a change" card. It creates the change project and opens it, ready to plan.
- **On a change project:** "Changes the previous project at commit <sha>", linking to the base.

## Consequences
- **Iteration without risk to verified work.** Every change is re-verified end to end, and the history of versions is a chain of linked projects, each with its own package.
- **Cheaper and more focused** than rebuilding: plans cover only the change, and agents see the current code.
- **Limits:**
  - Very large projects show the planner only part of the code (the paths are always complete).
  - A failing base file that no change task owns cannot get its own fix task: integration rework asks the owners of the named files it can find, or every change task's owner when none is found (ADR-0056). If that does not help, the project blocks for people, who can replan with a scope that covers the file.
  - Merging two parallel changes of the same base is not supported: each change starts from the base's verified commit.
