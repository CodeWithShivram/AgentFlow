# ADR-0039: Pricing snapshots and the usage tracker

- Status: Accepted
- Date: 2026-11-16
- Implements: Phase 7, Step 7.1 (centralized usage tracker and pricing snapshots). Handoff references:
  - §4: "Usage service records provider-reported usage and pricing snapshots";
  - §12: "Snapshot the applicable pricing data so historical cost reports remain reproducible", and "Do not estimate token counts and label them actual".
- Resolves: OD-21 (counts LiteLLM estimates for providers that do not report usage).
- Builds on: ADR-0013 (model gateway and `llm_usage`), ADR-0020 (LiteLLM adapter).
- Owner decisions (2026-11-16):
  - prices come from a curated file in the repository;
  - they are platform-wide;
  - the mock provider is priced at zero and labelled mock;
  - counts a provider does not report are marked estimated.

## Context
- **Calls were recorded but not priced.** Every model call already writes one `llm_usage` row through the gateway, with the provider-reported usage, but there were no prices. The cost columns stay empty until prices exist.
- **Estimated counts looked reported.** For providers that report no usage, LiteLLM fills in its own token estimates, which were indistinguishable from reported counts (OD-21).

## Decisions

### Prices are curated, versioned and immutable
- **The source.** `backend/app/llm/pricing.json` is reviewed like code and holds, per version:
  - a label;
  - a currency (one per version);
  - an aware `effective_from`;
  - a source note;
  - per provider, `reports_usage` and per model the input, output and (optional) cached-input prices per million tokens, plus a `mock` flag.
- **The loader.** `python -m app.llm.pricing load` validates the file strictly and writes it as a snapshot:
  - `pricing_versions` holds the label, currency, effective date, source, per-provider flags and the SHA-256 of the canonical content;
  - `model_prices` holds one row per provider and model.
- **Immutable versions.**
  - Loading an unchanged file again changes nothing.
  - Changing the file under a label already loaded is refused; a price change needs a new label.
  - Triggers refuse updates and deletes of both tables.
- **No network.** Nothing is fetched from the network, and LiteLLM's bundled price map is not used.
- **The version in force** at a moment is the latest whose `effective_from` has passed, the latest loaded on a tie.
- **Platform-wide.** Prices are the same for every workspace. They are not workspace data, so the tables have no RLS. The API and workers may only read them; the administrator writes them, like the built-in agents. Loading runs after migrations in setup, CI and the E2E stack.
- **The first list prices only the mock provider,** at zero and marked `mock`, which is never a real charge. It lists `ollama` as not reporting usage. No real provider prices were invented: they are added with their published prices in a new version.

### The usage tracker links each call to its price
- **One place.** `services.usage.record_model_call`, the only writer of `llm_usage`, does two more things in the same short transaction.
- **It links the price.** It links the price in force for the call's provider and exact model (`model_price_id`, a restricting foreign key). NULL means no price was known.
- **It records where the counts came from** (`usage_source`):
  - `reported`: counts are present and the list marks the provider as reporting its own usage;
  - `estimated`: counts are present for any other provider, including providers the list does not know and calls made before any list was loaded. Estimates are never labelled actual;
  - `not_reported`: no counts.
- **Existing rows** are backfilled: no counts become `not_reported`, the mock provider `reported`, and any other provider `estimated`.
- **No costs yet.** Cost columns stay NULL. Step 7.2 computes them from these facts and keeps estimated counts' costs apart from actual ones.

### API
`GET /pricing`, for any signed-in user, returns the list in force (404 `PRICING_NOT_LOADED` before a load).

## Consequences
- **Reproducible cost inputs.** Every recorded call names the exact immutable price it is to be costed at, and later price changes never alter it.
- **Real providers must be added before they can be costed.** Until then, their calls are `estimated` with no price.
- **A new reporting provider needs a new version** with its `reports_usage` flag, or its counts stay `estimated`.
