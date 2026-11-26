# ADR-0053: Project experience, weighted progress and time remaining

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.7; ADR-0046 decision 7; playbook §3 (user experience) and §11 (progress and ETA)
- Builds on: ADR-0048 (effort points), ADR-0051 (integration), ADR-0052 (packages)

## Context
- **What users should see.** A simple path: describe software, approve the plan, watch honest progress, and download the result. They shouldn't need to understand workflows, tasks or agents.
- **The rules from the playbook:**
  - progress is weighted by effort;
  - it counts only verified work;
  - it includes integration and packaging;
  - time remaining is a range with a confidence level, never a false-precision countdown.

## Decisions

### Progress (`GET …/workflows/{id}/progress`, `app/services/progress.py`)
- **Weights.** Each active task weighs its `effort_points` (1 without an estimate). Optional tasks cancelled at completion are left out.
- **Only verified work counts.** COMPLETED tasks count, which only platform verification reaches. Work in flight and agent self-reports count for nothing.
- **Milestones.** Software projects add the integration gate (15% of the task weight, at least 1) and the package (5%, at least 1). Each counts only once it passed or exists.
- **Never 100% early.** Progress reaches 100% only for a completed workflow; for a software project, also only once a package is READY. It is reported with an explanation ("X of Y weighted points verified complete").
- **Phases:** Planning, Awaiting approval, Building, Integrating, Packaging, Completed, Needs attention, Failed and Cancelled. They are derived from the workflow status, the plan status, the milestones and open requests for people.

### Time remaining
- **Samples.** The duration of each successful attempt of a completed task, divided by its effort points.
- **Range:**
  - low = remaining points × the 25th percentile ÷ parallelism;
  - high = remaining points × the 75th percentile × 1.5 (retries and review) ÷ parallelism;
  - with fewer than four samples, the minimum and maximum stand in for the percentiles;
  - parallelism is the workflow's `max_parallel_tasks`, capped by the unfinished task count.
- **Confidence.** `none` (no samples, so no numbers and an explanation instead), `low` (< 3), `medium` (< 10) or `high`.
- **Inputs.** Only stored records are used, so the estimate improves as work finishes and changes when retries or replans happen. Model latency and retries are inside the observed durations rather than modelled separately.

### Screens (frontend)
- **Navigation.** A **Projects** entry with three pages:
  - a list (software projects, via the workflow list's new `kind` filter);
  - **New project** (a single prompt, advanced settings collapsed);
  - a **project page**.
- **The project page shows:**
  - phase;
  - the progress bar labelled as an estimate, with its explanation;
  - the time-remaining range and its confidence;
  - task counts and a compact task list (effort, file scope);
  - a needs-attention callout;
  - integration runs (check outcomes, output as plain text);
  - deliverables with VERIFIED or PARTIAL badges and download links (artifacts API);
  - an ADMIN-only partial-package action.
- **Planning, approval and start** stay on the existing workflow page ("Plan & controls"), which remains the advanced view.

## Consequences
- **Estimates stay estimates.** Early estimates are labelled low-confidence or absent, never invented.
- **One UI for both kinds.** General workflows keep their existing UI. The same progress endpoint works for them too, without milestones.
- **Possible follow-up.** Folding plan approval into the project page is left as a later refinement; the existing controls are reused rather than duplicated.
