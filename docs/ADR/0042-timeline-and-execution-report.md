# ADR-0042: Event timeline and execution report

- Status: Accepted
- Date: 2026-11-19
- Implements: Phase 7, Step 7.4 (event timeline and execution report). Handoff references:
  - §1: a final report of plan, task history, agents, artifacts, verification, issues, retries, token usage, costs and timing;
  - §14 screen 9: the Final Workflow Report;
  - acceptance 16: the final report includes deliverables, decisions, failures, retries, approvals and metrics;
  - §4: reporting composes persisted facts and never invents metrics.
- Builds on: ADR-0009 (events), ADR-0030 and ADR-0031 (verification), ADR-0035 to ADR-0038 (requests for people), ADR-0040 and ADR-0041 (costs and analytics).
- Owner decisions (2026-11-19):
  - the report is available at any time and labelled "In progress" until the workflow finishes;
  - it is generated on read from stored records;
  - accepted deliverables are included as capped plain text;
  - it is a printable page plus a JSON API.

## Context
- **The timeline lagged behind the events.** The append-only event log was already shown on the workflow page, but newer event types (requests for people, pauses, requeued jobs) appeared only as raw names. The timeline could not be filtered.
- **There was no report.**

## Decisions

### Timeline
- **Categories.** Every event type belongs to exactly one category: status, planning, verification, people or recovery. They are defined in `app/workflows/event_categories.py`, and a test keeps the mapping complete.
- **Filters.** `GET …/events` takes optional `task_id` and `category` filters.
- **The page.** The timeline groups events by day, filters by category and task in the browser (the page already loads the events), and describes every event type in plain text: decisions, pause reasons, request subjects, issue codes, requeued jobs.

### Execution report
- **The endpoint.** `GET /workspaces/{id}/workflows/{workflow_id}/report` (VIEWER) returns `report/v1`. It is built on each read from stored records only, so it is always current and never invents a figure.
- **Final or in progress.** `final` becomes true once the workflow is completed, failed or cancelled.
- **Request and plan:**
  - the prompt, settings and timestamps;
  - the confirmed plan version;
  - each task with its dependencies, assigned agent and version, and status.
- **Execution:** every attempt with its agent and version, status, error code, timestamps, duration and verification results. Results are verifier, outcome and issue codes only, never the evaluator's findings.
- **Deliverables:** for completed tasks, the accepted (latest successful) attempt's outputs. They are agent text, shown as plain text and capped at 20,000 characters each, with a `truncated` flag.
- **Failures and recovery:**
  - retries (attempts after a task's first) and failed attempts;
  - reassignments and escalations, taken from the event log;
  - expired requests and failed background jobs;
  - every pause with its reason and actor.
- **Human interventions:** what was asked, status, decision and choice, the decider's role and dates. People's free text (answers, rejection notes) is left out: approval notes are admin-only history and viewers can read the report.
- **Metrics:** directly counted tasks and attempts by status, verifications passed and failed, and elapsed time from the workflow's own timestamps (until now while it runs).
- **Tokens and costs:** the workflow's costs and reconciliation (ADR-0040) and its usage by task, agent and model (ADR-0041).
- **The report page** (`/workflows/{id}/report`, linked from the workflow) prints cleanly through the browser's "print or save as PDF".

### A fix to Step 7.3's attribution
A semantic evaluation records the attempt it judges, so the 7.3 analytics counted its cost as the evaluated agent's, against ADR-0041. Calls whose purpose is one of the platform's (`prompt_analysis`, `plan_generation`, `semantic_evaluation`; checked against the jobs' constants by a test) now always count as `platform:<purpose>`.

## Consequences
- **Acceptance 16 holds.** A workflow's deliverables, decisions, failures, retries, approvals and metrics can be read and printed in one place, from its records.
- **No schema change.** A frozen copy of the report is not stored. Because its sources are append-only or immutable, generating it again gives the same content, apart from `generated_at` and, while running, `elapsed_seconds`.
- **Still to come.** Latency, retry-rate and failure-rate metrics are Step 7.5. Artifact files and a downloadable format are Phase 8.
