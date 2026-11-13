# ADR-0016: Plan DAG validation and preview

- Status: Accepted
- Date: 2026-11-13
- Implements: Phase 2, Step 2.4 (DAG validation and plan preview API)
- Builds on: ADR-0015 (plan drafts)
- Owner decisions (2026-11-13):
  - a cyclic model reply is invalid output and is retried;
  - skills no active agent provides are warnings;
  - the preview gets a separate "Plan draft" tab;
  - any version can be previewed, newest first.

## Decisions

### Validation (`app/planning/dag.py`)
The module holds pure functions with no I/O. The planning job (worker), the schema and the preview (API) all use it.
- **`find_cycle`.** Kahn's algorithm. It returns the tasks on or behind a cycle.
- **`stages`.** Longest-path levels: tasks in one stage can run in parallel.
- **`analyse_plan`.** Produces the stages and a list of issues:

| Code | Severity | Meaning |
|---|---|---|
| `DEPENDENCY_CYCLE` | error | The tasks wait on each other; no stages are produced |
| `REQUIRED_DEPENDS_ON_OPTIONAL` | warning | A required task waits for an optional one |
| `SKILL_NOT_AVAILABLE` | warning | No ACTIVE agent (workspace or built-in) offers the skill |
| `STAGE_EXCEEDS_PARALLEL_LIMIT` | warning | A stage has more tasks than `max_parallel_tasks`; the rest queue |

- **Schema.** `PlanProposal` now also rejects cycles. The schema moved to `app/planning/schema.py` so the API imports no gateway code.
- **Generation.** A cyclic reply fails validation, is recorded as `INVALID_OUTPUT` and is retried, so stored drafts are always acyclic.

### Preview API
- **Endpoint.** `GET /workspaces/{id}/workflows/{id}/plan-drafts/{version}` (Viewer) returns the full draft, its stages and its issues.
- **Agent coverage.** It is computed at preview time from the active versions of the workspace's ACTIVE agents and active built-ins, so it reflects later agent changes.
- **Errors.** An unknown version returns 404 `PLAN_DRAFT_NOT_FOUND`. Non-members get 404 through `workspace_role`. Row-level security still applies.

### UI
- **Plan draft tab:**
  - a version selector, with the latest version as the default;
  - the summary;
  - errors ("This draft cannot be used") and warnings ("Check before confirming"), naming the affected tasks;
  - stage columns with expandable task cards showing skills, required/optional, dependencies, deliverables and acceptance criteria;
  - the risks.
- **Requirements tab.** Its status card has a "Review draft" button that opens the preview.
- **Text.** All model text is rendered as plain text.

## Consequences
- **Step 2.5** (confirmation) will refuse drafts with errors and show the warnings again before creating tasks.
- **Plan size.** Validation is O(tasks²) at most, which is fine for 25-task drafts.
