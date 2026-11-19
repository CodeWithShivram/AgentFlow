# ADR-0041: Usage and cost analytics

- Status: Accepted
- Date: 2026-11-19
- Implements: Phase 7, Step 7.3 (per-workflow/agent/model breakdown and analytics API). Handoff references:
  - §11: analytics endpoints (overview, costs, tokens, agents, one per workflow);
  - §14: the Analytics screen ("Token usage", "Cost by workflow/agent/model");
  - acceptance 15: "Token usage and cost are attributed to workflow/task/agent/model";
  - critical scenario 17: totals reconcile with stored usage;
  - §4: "Reporting service composes persisted facts; it must not invent metrics".
- Builds on: ADR-0013 (usage records), ADR-0039 (pricing), ADR-0040 (costs and bases).
- Owner decisions (2026-11-19):
  - the handoff's paths, under the workspace;
  - Viewer and above may read analytics;
  - calls no agent made count as "platform" by purpose;
  - a basic Analytics page now, polished in Phase 8.

## Context
Every model call is a `llm_usage` row carrying its workflow, task and attempt, plus tokens, cost and cost basis (ADR-0040). Only one workflow's totals could be read. There was no view by agent or model, and nothing workspace-wide.

## Decisions

### Breakdowns from stored usage only
- **One aggregation.** `app/services/analytics.py` sums the workspace's `llm_usage` rows in SQL, grouped by the chosen key, currency and cost basis. In Python it then folds them into rows of:
  - calls and unpriced calls;
  - input, output and cached tokens;
  - costs per currency, with actual, estimated and mock kept apart, never added together.
- **Group keys:**
  - `workflow` and `task`: the call's own ids;
  - `model`: provider and exact model;
  - `purpose`;
  - `day`: UTC date;
  - `agent`: through the call's attempt (`agent_executions.agent_id`).
- **Platform calls.** Calls no agent made (analysis, planning, semantic evaluation) group as `platform:<purpose>`, so every breakdown adds up to the same totals.
- **Labels** come from the workspace's own rows: a workflow's prompt start, a task title, an agent name. Calls whose workflow, task or agent has gone are labelled "Removed …".
- **Ordering and limits.** Rows are ordered by calls (days by date) and capped by `limit` (default 100, at most 500), with `truncated` set when rows were cut. Totals always cover every call in the range.
- **The range** is `from` (inclusive) to `to` (exclusive), with a time zone, at most 366 days, defaulting to the last 30 days. Anything else is 422 `ANALYTICS_RANGE_INVALID`.

### Endpoints
All endpoints are under `/workspaces/{id}/analytics`, for VIEWER and above, and non-members get 404:
- `GET /overview`: totals, plus how many workflows and agents made calls;
- `GET /costs?group_by=workflow|task|agent|model|purpose|day&workflow_id=&limit=`: rows with calls, tokens and costs;
- `GET /tokens` (same parameters): the rows' calls and tokens only;
- `GET /agents`: the agent breakdown;
- `GET /workflows/{workflow_id}`: one workflow's whole history by task, agent and model.

### Frontend
An "Analytics" sidebar page offers ranges of 7, 30 or 90 days or a year, the overview, and tables by workflow, agent and model. Each cost basis has its own column, and unpriced groups show "—" rather than zero. Names are shown as plain text, and workflow names link to their workflow.

## Consequences
- **Attribution and reconciliation hold.** Usage and cost are attributed to workflow, task, agent and model, and every breakdown reconciles with the overview; tests check both.
- **No new tables or indexes.** Breakdowns aggregate on read, using `ix_llm_usage_workspace_id_created_at`. Precomputed rollups can come later if volumes need them.
- **Not covered yet.** Execution duration and retry and failure rates are Step 7.5. Charts and the dashboard summary are Phase 8.
