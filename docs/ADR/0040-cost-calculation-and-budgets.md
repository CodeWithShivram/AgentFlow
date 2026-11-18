# ADR-0040: Cost calculation, reconciliation and budgets

- Status: Accepted
- Date: 2026-11-18
- Implements: Phase 7, Step 7.2 (cost calculation and reconciliation, actual vs estimated). Handoff references:
  - §12: the cost formula; snapshot pricing; "Never present an estimate as an actual provider charge"; failed calls and retries are tracked, never double counted;
  - critical scenario 13: "Budget cap prevents further dispatch";
  - critical scenario 17: "Token/cost totals reconcile with stored usage records";
  - §6: "Budget exhaustion pauses or terminates according to workflow policy".
- Builds on: ADR-0034 (the deadline pause), ADR-0036 (pause reasons), ADR-0039 (pricing snapshots and usage sources).
- Owner decisions (2026-11-18):
  - cached input tokens use the cached price, or the input price when there is none;
  - reaching the budget pauses the workflow once;
  - a budget in another currency than the prices is shown as not comparable and not enforced;
  - actual plus estimated spend counts toward the budget, and mock spend never does.

## Context
Since ADR-0039 every model call names the price in force for it and where its token counts came from, but no cost was computed. Workflows had a `budget_limit` that nothing measured or enforced.

## Decisions

### Cost per call, computed once from its snapshot
- **The formula.** `app.llm.costing` (pure, importable by the API) computes, per million tokens:
  - input: `(input − cached) × input price + cached × (cached price, or the input price when the list has none)`;
  - output: `output × output price`.
- **Rounding and range.** Cached tokens are capped at the input count. Amounts use exact decimal arithmetic and are rounded half up to 8 places, the `Numeric(18, 8)` columns.
- **When a cost is computed.** Only when the provider gave both input and output counts.
- **The basis.** A new `llm_usage.cost_basis` says what the cost is:
  - `actual`: reported counts at a real price;
  - `estimated`: estimated counts (OD-21);
  - `mock`: the mock provider's price, never a real charge.
- **Written at record time.** `record_model_call` fills `input_cost`, `output_cost`, `total_cost`, `currency`, `pricing_version` and `cost_basis` together, in the same transaction that records the call.
  - Rows stay append-only for workers.
  - A check ties the basis to the costs.
  - A call without a price, or without counts, stays unpriced (all NULL).
- **Retries and failures.** Each attempt is its own recorded call, so retries are counted once each, never twice. A failed or invalid-output call that the provider reported usage for is costed, because it was billed.
- **Existing rows.** Migration `0029` costs usage already linked to a price, with the same rule in SQL.

### Reconciliation
- **What it recomputes.** `costing.workflow_costs` recomputes every priced call of a workflow from its stored tokens and its own linked snapshot, and compares that with the stored costs, currency, pricing version and basis.
- **What it totals.**
  - counts per status, unpriced calls and tokens;
  - totals per currency and basis, keeping actual, estimated and mock apart.
- **The endpoint.** `GET /workspaces/{id}/workflows/{workflow_id}/costs` (VIEWER) returns these figures, the budget status, `checked_calls`, `mismatched_calls` and `reconciled`.
- **Persisted facts only.** Every figure is composed from persisted facts. A stored cost that no longer matches its snapshot is reported, never hidden or corrected silently.

### Budgets
- **What counts.** A workflow's spend is its actual plus estimated cost in its own currency. Mock costs never count.
- **Currency.** No exchange rates exist, so a budget in another currency than the priced calls is "not comparable" and not enforced.
- **The pause.** When counted spend is above zero and reaches `budget_limit`, `engine.pause_at_budget` pauses a RUNNING workflow:
  - actor SYSTEM, a new `PauseReason.BUDGET_REACHED`, reason `BUDGET_REACHED`;
  - nothing new is dispatched, and running attempts finish.
- **When it is checked:**
  - by the runner after each attempt, since the attempt's calls are already recorded;
  - by the recovery sweep every minute, which also catches planning and evaluation calls.
- **Only once.** Like the deadline (ADR-0034), it pauses once: resuming past the budget is a person's deliberate choice. It is never lifted automatically.

### Frontend
- **Settings tab.** "Budget spent" shows counted spend against the budget, or "not comparable", or "No budget set".
- **"Model costs" card.** It shows actual, estimated and mock costs apart, the calls by status, unpriced calls, tokens and the reconciliation result.
- **Pause banner.** It explains a budget pause.

## Consequences
- **Scenarios 13 and 17 hold.** A budget cap stops further dispatch, and cost totals reconcile with the stored usage.
- **Real costs need real prices.** Only priced models produce costs. Until real providers are added to the price list (ADR-0039), real calls stay unpriced, show as unpriced and do not count toward a budget.
- **The cap is soft.** Attempts already running when the budget is reached finish, as with the deadline.
