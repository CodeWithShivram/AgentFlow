# ADR-0043: Latency, retry and failure metrics

- Status: Accepted
- Date: 2026-11-19
- Implements: Phase 7, Step 7.5 (latency, retry and failure metrics). Handoff references:
  - §14 screen 8 (Analytics: "Execution duration", "Retry/failure rates");
  - §4: reporting composes persisted facts and never invents metrics.
- Builds on: ADR-0013 (model call records with latency), ADR-0019 (tool invocations with duration), ADR-0029 and ADR-0032 (attempts and retries), ADR-0030 and ADR-0031 (verification), ADR-0041 (analytics ranges and roles).
- Owner decisions (2026-11-19):
  - exact median, 95th percentile and maximum, plus the mean;
  - the retry rate is attempts after a task's first, over all attempts;
  - breakdowns by agent, model and tool;
  - a measurement counts in the period it finished, and cancelled attempts are left out of failure rates.

## Context
- **The data was already recorded.** Every model call has a status and latency. Every attempt has a status, an attempt number and start and end times. Every tool call has a status and duration. Verification results and workflow start and finish times are stored too.
- **Nothing summarised it.** No view turned these into durations or rates.

## Decisions

### `GET /workspaces/{id}/analytics/metrics`
- **Access and range.** VIEWER and above; the same range rules as ADR-0041 (with a time zone, at most 366 days, defaulting to the last 30).
- **Computed in SQL.** Everything comes from stored records in `app/services/metrics.py`, with no new tables.
- **Model calls,** counted when recorded:
  - counts by status;
  - failure rate: failed and invalid-output calls over all calls;
  - latency in ms;
  - the same per model.
- **Agent attempts,** counted when they finished:
  - counts by status, and in-flight (running) attempts;
  - failure rate: failed and timed-out attempts over finished attempts that were not cancelled;
  - retry rate: attempts with `attempt_number > 1` over finished attempts;
  - duration in seconds;
  - the same per agent.
- **Tasks:** those completed in the range, and the share whose accepted attempt (the latest successful one, ADR-0034) was their first.
- **Verification:** pass rates of the deterministic checks and of the semantic evaluation, from results stored in the range.
- **Tool calls:** counts by status, failure and denial rates, duration in ms, and the same per tool.
- **Workflows:**
  - those finished in the range (completed, failed or cancelled), by status;
  - in-flight ones (running, paused or blocked);
  - run time from start to finish, for those that started.
- **Rate format.** Every rate carries its numerator and denominator. Its value is null, "not measured", when the denominator is 0; it is never presented as 0%.
- **Distribution format.** Every distribution carries its unit and sample count. Its median and 95th percentile are exact (PostgreSQL `percentile_cont`), together with the maximum and mean, all null without samples.
- **Group limit.** Groups are capped at 50, ordered by count.

### Frontend
A "Reliability and latency" section on the Analytics page, using the same range tabs, shows:
- each rate as "25% (1 of 4)";
- each distribution as "median · 95th · max (samples)";
- unmeasured values as "Not measured";
- tables per agent and per model.

## Consequences
- **The handoff's Analytics screen is complete:** token usage, cost by workflow, agent and model, execution duration, and retry and failure rates.
- **Phase 7 is complete.**
- **Percentiles are computed on read.** That is exact and needs no rollups at local volumes. Precomputed aggregates can follow if volumes need them.
- **Workflows are not exact for a period.** Workflow run time and in-flight counts reflect the workflows' current status. A workflow resumed after a pause keeps its original start time.
