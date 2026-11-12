# ADR-0013: Model gateway and mock model adapter

- Status: Accepted
- Date: 2026-11-12
- Implements: handoff §3 (provider/model gateway, mock adapters), §5 `llm_usage`, §12 (usage accounting); Phase 2, Step 2.1
- Owner decisions (2026-11-12): usage is persisted from the first step; models are called from workers only, through a sync gateway; the mock is scripted, with deterministic defaults.

## Context
Phase 2 adds planning by a language model. The handoff requires:
- every model call goes through one gateway;
- each call records provider-reported usage;
- nothing estimated is ever labelled actual;
- deterministic mock adapters are used for tests.

Real providers arrive behind LiteLLM in Phase 3. Pricing snapshots and cost calculation arrive in Phase 7.

## Decisions

### One gateway, in workers only
- **Where it lives.** `app/llm/gateway.py` holds `ModelGateway.complete(context, request, response_format=None)`. It is the only way to call a model.
- **Synchronous, workers only.** It is synchronous and runs only in Celery workers, through `get_model_gateway()`, one per process. The API never waits on a model. Planning (Steps 2.2–2.4) will run as background jobs dispatched through the existing outbox.
- **Caller inputs.**
  - A `CallContext`: workspace, plus optional workflow, task and execution.
  - A `ModelRequest`: a `purpose` slug, chat messages, the model `profile`, temperature and max output tokens.
  - Optionally a Pydantic `response_format`.
- **Model selection.** The gateway resolves the model from the profile. Until per-profile routing exists, only `default` is known, and it maps to `MODEL_NAME`. The provider comes from `MODEL_PROVIDER`.

### Adapters
- **Contract.** `ModelAdapter` (`app/llm/types.py`) is the provider contract. It returns the reply text, the usage exactly as the provider reported it (`None` when not reported, never estimated), and the provider request ID. Adapters never record anything.
- **`MockModelAdapter`** (`app/llm/mock.py`) is the only provider now (`MODEL_PROVIDER=mock`, the default). It works as follows:
  - **Scripts.** Tests script replies per purpose; they are consumed in order and may be exceptions that simulate provider failures.
  - **Defaults.** Without a script, a purpose listed in `DEFAULT_RESPONSES` gets its canned reply. Later steps register their purposes with schema-valid replies, so development and E2E runs work without API keys.
  - **Unscripted calls.** An unscripted structured call fails loudly. An unscripted text call gets a deterministic placeholder.
  - **Usage.** It is reported like a provider's, from word counts, with a request ID derived from the call. Identical calls produce identical records.

### Usage accounting (`llm_usage`)
- **One row per call:** `SUCCEEDED`, `FAILED` (the provider raised) or `INVALID_OUTPUT` (a reply arrived but failed its schema). The row holds:
  - purpose, provider and the exact model;
  - the provider-reported token counts, plus `other_usage`;
  - the provider request ID and latency;
  - a short error code, never provider text.
- **Its own transaction.** `app/services/usage.py` commits each record in a separate transaction. A call is accounted for even when the caller's work later rolls back, because the provider already did, and billed, the work.
- **Costs.** `input_cost`, `output_cost`, `total_cost`, `currency` and `pricing_version` stay NULL ("not priced") until Phase 7 pricing snapshots. A check constraint makes them all-or-nothing.
- **Other constraints:**
  - tokens are non-negative;
  - an error code is present exactly when the call did not succeed;
  - a task implies a workflow;
  - composite foreign keys keep the workflow and task inside the row's workspace.
- **Security and grants.**
  - Row-level security follows ADR-0012: tenants see only their workspaces' rows, and workers have the platform policy.
  - Workers get `SELECT, INSERT` only, so recorded history cannot be rewritten.
  - The API can read the usage of its own workspaces, for later reporting.

### Untrusted output
- **Validation.** Structured replies must validate against the requested schema, via `model_validate_json`. Otherwise the gateway raises `ModelOutputInvalidError`.
- **No echoed output.** Error messages name the purpose and schema but never echo model output.
- **Logs.** They carry metadata only: purpose, provider, model, status, latency, tokens and identifiers. Prompts and outputs are never logged or stored in usage records.

## Consequences
- Later steps call `get_model_gateway().complete(...)` from worker tasks and register mock defaults for their purposes. Retries are the caller's decision; each attempt is recorded separately.
- LiteLLM (Phase 3) becomes another `ModelAdapter`. Pricing (Phase 7) fills the cost columns of new rows, together with the pricing version used.
- There is no timeout or retry policy in the gateway yet. They arrive with real providers, where they mean something.
