# ADR-0049: Project workspaces and file ownership enforcement

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.3; ADR-0046 decision 3 ("agents propose; the platform writes")
- Builds on: ADR-0022 (Developer outputs), ADR-0030 (deterministic verification), ADR-0048 (file scopes)
- Supersedes in part: ADR-0022. Accepted Developer proposals are now applied, by the platform.

## Context
- **What's missing.** Software projects need a real source tree that later steps can check (9.5) and package (9.6).
- **The playbook asks** that agents work in isolation, never overwrite one another, and never write outside their task.
- **What exists.** Agents in this platform have no filesystem at all. They return proposals.

## Decisions

### Agents still never write
- **What's unchanged.** Developer runtimes keep returning `proposed_files` and `proposed_tests`, and their platform note still says nothing was executed or written.
- **What's new.** The platform applies a proposal only after the task has been accepted by platform verification (COMPLETED; ADR-0034).
- **Isolation.** Each agent works on its own attempt, and no agent shares a working tree.

### Scope is verified before acceptance (`deterministic/v2`)
- For a task with a `file_scope`, the deterministic checks add `file_scope`:
  - the submission's outputs must parse as Developer outputs (`FILES_MISSING` otherwise);
  - every proposed path must be inside the task's scope (`FILE_OUTSIDE_SCOPE`).
- A failure goes through the normal rework policy (ADR-0032).
- Tasks without a scope are unaffected, apart from the version name.

### Contract v4: scope and dependency files
- **New fields.** `file_scope`, `stack_profile` and `dependency_files`.
- **`dependency_files`** holds the accepted files of the tasks this one depends on, from their latest successful attempts.
  - Limits: at most 40 files and 120,000 characters in total, taken in dependency and path order.
  - It reaches models only inside `<task_contract>`, like the rest of the contract.
  - It holds agent output that already passed verification, and stays data, not instructions.
- **Purpose.** Dependent tasks (tests, integration) can write code that fits what was built before them.

### Workspace (`app/projects/workspace.py`)
- **Location.** One local Git repository per project at `PROJECT_WORKSPACE_ROOT/<workspace_id>/<workflow_id>`, branch `main`.
  - The root defaults to `~/.agentflow/workspaces`.
  - It must be absolute and outside the repository.
- **Applying.** Applying a task makes the files under its scope match its accepted files exactly: proposed files are written atomically, and files of that scope that are no longer proposed are removed. Then one commit is made as `AgentFlow <agentflow@localhost>`. Re-applying the same files changes nothing.
- **Re-checks on every path:**
  - Developer path rules;
  - inside the scope;
  - no `.git`, `.gitattributes` or `.gitmodules`;
  - no symlink or junction from the project folder down;
  - the resolved path stays inside the project;
  - at most 200,000 bytes per file.
- **Git runs hardened:**
  - no system or global config, an empty hooks folder, no signing, no credential helper or prompts, no symlinks or fsmonitor;
  - a minimal environment and a timeout;
  - `add --force`, so a project's `.gitignore` cannot hide platform-written files.

  Git output never reaches logs or clients; failures carry fixed codes.

### Apply job (`project.apply`, `app/projects/apply_job.py`)
- **Trigger.** The engine writes `project.apply` (identifiers only) whenever a software project's task becomes COMPLETED.
- **What the job does:**
  1. consumes the event idempotently;
  2. locks the workflow, so applies within one project are serialised;
  3. applies the latest successful attempt;
  4. records one `workspace_applications` row per attempt (unique `execution_id`).
- **The row** is APPLIED with commit, head, counts and a tree hash, or FAILED with a fixed code: `OUTPUTS_INVALID`, `FILE_OUTSIDE_SCOPE`, `GIT_UNAVAILABLE`, `GIT_FAILED` or `WORKSPACE_PATH_REFUSED`.
- **The table** is append-only (trigger), with RLS tenant and platform policies. Workers get SELECT and INSERT.

## Consequences
- **Order and coherence.** Scopes are disjoint, so the order in which tasks are applied does not change the result. Dependents still see their dependencies' files through the contract, not the workspace.
- **No silent failures.** A FAILED application is recorded, not hidden. The integration gate (9.5) requires an APPLIED row for every completed task before it checks the tree.
- **Still not executed.** The tree is untrusted generated code. Step 9.4 adds the sandbox that executes it.
- **New runtime requirement.** Git must be on the worker's PATH. Without it, applications fail with `GIT_UNAVAILABLE` (fail closed).
