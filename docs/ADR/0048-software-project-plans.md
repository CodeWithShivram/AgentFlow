# ADR-0048: Software project workflows and plan schema version 2

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.2 (playbook "project generation domain"); ADR-0046 decision 2
- Builds on: ADR-0015 (plan drafts), ADR-0016 (DAG validation), ADR-0017 (confirmation), ADR-0033 (replanning)

## Context
- **What later steps need.** Software projects must say what they build and on which stack. Each task must say which files it alone may write, so that:
  - the platform can apply proposals without conflicts (9.3);
  - the sandbox can check them with a known toolchain (9.4–9.5);
  - progress can be weighted (9.7).
- **What existed.** Plans had only skills, deliverables, criteria and dependencies.

## Decisions

### A workflow kind, not a new aggregate
- **`workflows.kind`.** `GENERAL` (the default, so nothing changes for existing workflows) or `SOFTWARE_PROJECT`. It is chosen at creation (`POST …/workflows` with `kind`).
- **`workflows.project_spec`.** The confirmed plan's project, allowed only for software projects (check constraint).

### Stack profiles (`app/projects/profiles.py`)
- **What a profile fixes.** A curated, immutable registry. Each profile fixes:
  - a runtime;
  - the only third-party dependencies a project may declare;
  - its shared files (manifests, README);
  - its test folders.
- **Initial profiles:** `python-3.12` (FastAPI, pydantic, httpx, uvicorn, SQLAlchemy, pytest) and `node-24-typescript` (Express, zod, TypeScript, Vitest).
- **Why curated.** The Step 9.4 sandbox images contain exactly these packages and run offline. A changed dependency set is a new profile id.

### Plan schema version 2 (`app/planning/schema.py`)
- **Project.** `project` holds `name` (slug), `stack_profile` (a known profile), `architecture` and `assumptions`.
- **Per task.** `file_scope` lists exact files, or folders ending in `/`. Entries pass the Developer output path rules (ADR-0022) and must not overlap within a task. `effort_points` runs from 1 to 13.
- **Rules:**
  - every task has a scope and an effort;
  - scopes of different tasks never overlap, compared case-insensitively (`app/projects/scope.py`);
  - version 1 plans carry neither field.
- **Shared files.**
  - **At most one owner.** Each of the profile's shared files has at most one owner, because scopes are disjoint.
  - **A first plan owns them all.** The planner retries with `PLAN_SHARED_FILE_UNOWNED`, and confirmation refuses `SHARED_FILE_UNOWNED`.
  - **Replans and appended plans may leave them** with kept tasks that already own them.

### Planner (`app/planning/plan.py`, prompt `software_plan/v1`)
- **Prompt.** For software projects the system prompt adds the software rules, and the user message adds `<stack_profiles>` as delimited data. A confirmed project's replan sees only its own profile.
- **Kind checks.** A reply of the wrong kind is invalid output and is retried:
  - `PLAN_PROJECT_MISSING`;
  - `PLAN_PROJECT_UNEXPECTED`;
  - `PLAN_PROFILE_CHANGED`.
- **Mock provider.** It drafts a deterministic three-task project (setup owning the shared files and `.env.example`, `src/`, `tests/`) and matching version 2 replans.

### Confirmation (`app/services/tasks.py`)
- **What is copied.** `file_scope` and `effort_points` go onto the tasks, and the project into `workflows.project_spec`.
- **What is refused** (`PLAN_DRAFT_INVALID` with codes):
  - a plan of the wrong kind (`PROJECT_PLAN_REQUIRED`, `PROJECT_PLAN_UNEXPECTED`);
  - a changed stack profile (`PROJECT_PROFILE_CHANGED`);
  - new scopes overlapping tasks that are kept (appended plans, a replan's completed tasks): `FILE_SCOPE_OVERLAP`;
  - a shared file without an owner (`SHARED_FILE_UNOWNED`).

### API
- **Workflow responses** include `kind` and `project_spec`.
- **Task responses** include `file_scope` and `effort_points`.

## Consequences
- **Ownership is known before any code exists.** Step 9.3 enforces it when proposals are applied.
- **Effort points are inputs, not measurements.** Step 9.7 combines them with observed durations for progress and time-remaining ranges, and labels them as estimates.
- **Two stacks only.** Projects outside the profiles cannot be planned; adding a profile needs a matching sandbox image (9.4).
- **Not in the UI yet.** The frontend does not offer the kind yet (Step 9.7). The API and stored data are ready.
