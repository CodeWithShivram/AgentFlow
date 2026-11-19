# ADR-0045: Analytics and final report UI

- Status: Accepted
- Date: 2026-11-19
- Implements: Phase 8, Step 8.2 (analytics and final report UI). Handoff references:
  - §14 screen 1 (Dashboard: "Token/cost summary when available");
  - §14 screen 8 (Analytics: token usage, cost by workflow/agent/model, duration, retry and failure rates);
  - §14 screen 9 (Final Workflow Report: request, decomposition, dependency graph, assignments, execution timeline, verification, failures and recovery, interventions, token and cost breakdown);
  - acceptance 16: the final report includes deliverables, decisions, failures, retries, approvals and metrics.
- Builds on: ADR-0040 (cost bases never summed), ADR-0041 (analytics), ADR-0042 (timeline and report), ADR-0043 (metrics), ADR-0044 (task graph).
- Owner decisions (2026-11-19):
  - a daily usage chart, hand-built with no new dependency, with days cut in the viewer's time zone;
  - the chart shows tokens and calls only; costs stay in the tables;
  - the report fetches its timeline through the existing events endpoint, with no new report version;
  - the dashboard shows the attempt success rate.

## Context
- **Phase 7 built most of the screens.** The Analytics page had an overview, breakdowns and reliability metrics. The execution report covered the request, tasks, attempts, deliverables, recovery, interventions and costs.
- **The gaps:**
  - the dashboard still said spend and tokens were not measured;
  - nothing showed usage over time, though `group_by=day` existed (in UTC);
  - the report received `metrics` and `requirements` but did not show them, and it had no dependency graph or timeline;
  - the proposals list briefly showed "No proposals" while a new filter loaded.

## Decisions

### Days in a time zone (the only backend change)
- **`tz` query parameter.** `GET …/analytics/tokens` and `…/costs` take an optional `tz`, an IANA zone name such as `Europe/Paris`. It cuts days for `group_by=day` (`date(timezone(tz, created_at))` in PostgreSQL); other groupings ignore it.
- **Validated.** The name must be one of Python's `zoneinfo.available_timezones()`; anything else is 422 `ANALYTICS_TIME_ZONE_INVALID`. The name reaches SQL only as a bound parameter.
- **Default unchanged.** Without `tz`, days stay UTC. Responses name the zone they used (`time_zone`).

### Analytics: "Usage over time"
- **One measure, one axis.** Columns show tokens (in plus out) per day in the viewer's zone (`Intl` time zone). Calls have a different scale, so they appear in the readout and the table, never on a second axis.
- **Costs stay in the tables.** Actual, estimated and mock amounts are never added together (ADR-0040), so a single cost per day would be misleading.
- **Every day has a column.** Days without calls are real zeros, since nothing was recorded. Past 31 days, columns are 7-day buckets counted back from the last day.
- **Readable without the chart.**
  - A readout line shows the range totals, or a column's day, tokens in and out, and calls on pointing.
  - The keyboard moves through the columns with the arrow keys; Escape clears.
  - "Show as table" lists every bucket.
  - The columns themselves are `aria-hidden`.
- **No new dependency.** Bars are styled elements in the theme's primary colour.

### Dashboard: "Usage, last 30 days"
- **Replaces the stale note.** It shows model calls, tokens, actual, estimated and mock costs apart, and unpriced calls, from `analytics/overview`.
- **Attempt success rate.** Succeeded attempts over finished attempts that were not cancelled, from `analytics/metrics`, shown with its counts. It is "Not measured" (never 0%) when nothing finished.
- **Links to Analytics.**

### Report
- **Requirements.** The analysed request, when there is one, shown like the Requirements tab.
- **Dependency graph.** The ADR-0044 layout, static and printable, sharing its arrow drawing (`GraphEdges`); the "After: …" line carries the same facts.
- **Execution timeline.**
  - It lists the workflow's events oldest first, through the existing events endpoint, one request of at most 500 events.
  - When there are more, it says so and links to the workflow's Timeline.
  - People's free text (an event's `reason`) is left out, as everywhere in the report (ADR-0042).
- **Metrics.** Tasks and attempts by status, verification results passed and failed, and elapsed time.
- **No new report version.** The report API is unchanged; the timeline is fetched by the page.

### Proposals list
While a new filter loads, an empty page kept from the previous filter shows the loading state instead of "No proposals".

## Consequences
- **The handoff's Dashboard, Analytics and Final Report screens are complete.** The report's "Artifacts" waits for Step 8.3; it shows deliverables meanwhile.
- **The printed report's timeline is the page's.** A workflow with more than 500 events prints the first 500 and says so.
- **Zone names follow the server's tz database.** A zone the browser knows but the server does not is refused, so the chart shows its error state. An unlikely case, since both use IANA names.
