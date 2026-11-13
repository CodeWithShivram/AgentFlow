# ADR-0020: LiteLLM provider adapter and provider usage capture

- Status: Accepted
- Date: 2026-11-13
- Implements: Phase 3, Step 3.3 (LiteLLM integration behind the gateway adapter, provider usage capture); handoff §3 (LiteLLM as provider gateway, provider and model from environment variables), §12 (provider-reported usage), §13 (secrets)
- Builds on: ADR-0013 (model gateway, usage records)
- Owner decisions (2026-11-13):
  - any LiteLLM provider, configured through settings;
  - offline verification only;
  - one model for every profile.

## Decisions

### Dependency
- **Version.** `litellm` 1.109.1, locked in `uv.lock`. It is required by handoff §3.
- **Weight.** It is a large dependency: the `openai` SDK, `tiktoken`, `tokenizers`, `huggingface-hub`, `boto3`, `aiohttp` and `httpx` come with it.
- **Where it loads.** It is imported only when `MODEL_PROVIDER=litellm`, by `create_adapter` in worker processes. The API and mock-mode workers never load it.

### Configuration (`Settings`)
- **`MODEL_PROVIDER`.** It is `mock` (default) or `litellm`.
- **`MODEL_NAME`** is a LiteLLM model string (for example `anthropic/<model>`). With one model for all profiles, every profile (agents and planning use `default`) resolves to it.
- **Startup check.** The adapter asks LiteLLM to route `MODEL_NAME` when it is created, and fails fast if it can't.
- **`MODEL_API_KEY`** (secret) is required unless `MODEL_API_BASE` is set. That optional endpoint is for a local server such as Ollama, or a proxy, and must be an http(s) URL.
- **Key handling.** The key is passed explicitly on each call. It is redacted from logs, and it is rejected in production when it is a placeholder.
- **`MODEL_TIMEOUT_SECONDS`** defaults to 60 s, with a maximum of 600.
- **Defaults.** Mock stays the default, so development, E2E and CI need no key.

### Adapter (`app/llm/litellm_adapter.py`)
- **Contract.** It implements `ModelAdapter`. The gateway still owns usage records, output validation and logging (ADR-0013).
- **Retries.** `max_retries=0` and `num_retries=0`: LiteLLM never retries on its own. A retry is the caller's decision, and each billed request is one gateway call and one `llm_usage` row.
- **`drop_params=True`.** Models differ in the settings they accept. For example, LiteLLM allows only `temperature=1` for `claude-sonnet-5-5`, and without this flag the call is refused. Unsupported settings are dropped instead.
- **Structured output.** When LiteLLM reports schema support for the model, the request includes the Pydantic model's JSON Schema (`strict: false`, because providers differ in what strict schemas accept). The gateway validates every reply against the Pydantic model regardless.
- **Provider name.** The provider recorded in `llm_usage` is the routed provider (for example `anthropic`), not "litellm".

### Hardening
- **Offline mode before import.** `LITELLM_OFFLINE_MODE=true` and `LITELLM_LOCAL_MODEL_COST_MAP=true` are set before LiteLLM is imported. Otherwise it downloads its price map from GitHub at import, and can fetch tokenizers or other data on its own. The adapter module is the only code that writes these environment variables.
- **No data leaves through LiteLLM's extras:**
  - logging callbacks and integrations are cleared;
  - `turn_off_message_logging` and `suppress_debug_info` are on;
  - tokenizer downloads are disabled.
- **Logging.** LiteLLM attaches its own stream handler. The adapter removes it and routes LiteLLM's loggers through the application's redacting JSON handler at WARNING, so prompts and outputs are never logged.
- **Warning filter.** Pydantic warns about `ReadOnly` items in LiteLLM's internal types on first use. Only that exact message is filtered.
- **Errors.** Provider exceptions become `MODEL_CALL_FAILED` in the gateway. The usage row and logs carry the code and exception type only, never provider text, which can include request details.

### Provider usage capture
- **Columns.** `input_tokens`, `output_tokens` and `cached_input_tokens` come from the provider's `usage` as LiteLLM normalises it (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`).
- **`other_usage`.** It holds every other reported field (`total_tokens`, cache-write and reasoning details, and so on) plus `reported_via: "litellm"`.
- **Request ID.** The response `id` is stored as `provider_request_id`.
- **Costs.** LiteLLM's own cost estimate (`response_cost`) is never read. Costs stay NULL until Phase 7 pricing snapshots, so no estimate is labelled actual.

## Verification
- **Offline only.** Tests use LiteLLM's real `completion` with `mock_response`, which makes no network call and needs no key:
  - arguments: key, timeout, retries off, schema;
  - usage capture and no cost;
  - invalid output and provider failures (one call, no retries, no leaked key);
  - settings validation and the hardening flags;
  - an integration test writing an `llm_usage` row as the worker role.
- **Not verified yet.** No live provider call was made. Behaviour against each real provider (schema handling, dropped settings, usage fields) is untested here. The owner can try it by setting `MODEL_PROVIDER=litellm`, `MODEL_NAME` and `MODEL_API_KEY` in `.env`.

## Consequences
- **Planning.** With `MODEL_PROVIDER=litellm`, prompt analysis and plan drafting immediately use the configured model. Specialist agents (Step 3.4) will call the same gateway.
- **Known limitation: estimated usage.** For providers that do not report usage, LiteLLM may fill token counts with its own tokenizer without marking them. For example, Ollama falls back to estimating when `prompt_eval_count` is missing. AgentFlow cannot tell these apart, so they are recorded as LiteLLM reports them, with `reported_via: "litellm"`. Phase 7 pricing must treat such providers' counts as unverified (OD-21).
- **Credentials in the environment.** LiteLLM may still read provider-specific environment variables (such as `ANTHROPIC_API_KEY`) when no key is passed. That happens only with `MODEL_API_BASE` and no `MODEL_API_KEY`.
- **Not added yet.** Per-profile routing is deferred until an agent needs a second model. Streaming is not used.
