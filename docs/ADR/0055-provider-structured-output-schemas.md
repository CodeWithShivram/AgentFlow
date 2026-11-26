# ADR-0055: Relaxed schemas for providers' structured output

- Status: Accepted
- Date: 2026-11-26
- Amends: ADR-0020 (LiteLLM provider adapter)
- Found by: the first live project run on Gemini (`gemini/gemini-3.5-flash-lite`)

## Context
- **What the adapter sent.** The LiteLLM adapter passed each response model's full JSON Schema as the provider's structured-output schema (`strict: false`).
- **What failed.** Gemini compiles that schema into a decoding constraint. It rejected AgentFlow's prompt-analysis and plan schemas with HTTP 400: "The specified schema produces a constraint that has too many states for serving". The cause is many bounds: string lengths, array sizes, numeric ranges, patterns and formats.
- **The effect.** Every analysis failed (`MODEL_CALL_FAILED`) before any project could be planned.
- **What already covers the gap.** The gateway validates every reply against the full Pydantic model, and invalid output is retried with corrections, so the provider-side bounds were never the safety net.

## Decision
- **What the provider receives.** `provider_schema(model)` in `app/llm/litellm_adapter.py`: the model's JSON Schema without upper and value bounds (`maxLength`, `maxItems`, `maxProperties`, `maximum`, `minimum`, `exclusiveMaximum`, `exclusiveMinimum`, `multipleOf`, `pattern`, `format`, `uniqueItems`) and without `title` annotations.
- **Lower bounds stay.** `minItems`, `minLength` and `minProperties` are kept: they cost providers little and tell the model an answer may not be empty. Without them, the live run's Developer replies came back empty.
- **What is kept:** types, properties, required fields, enums, references and descriptions.
- **Field names are safe.** Keys inside `properties`, `$defs`, `definitions` and `patternProperties` are names and are never removed, even when a field is called `title` or `pattern`.
- **Same for every provider.** One behaviour is simpler to reason about and test. The models AgentFlow targets don't use length bounds while decoding anyway.
- **Validation is unchanged.** The gateway validates the reply against the full model (`response_format` in `ModelGateway.complete`), and agents and planners keep their corrective retries.

### Typed agent replies
- **The problem.** Model agents asked for `AgentSubmission`, whose `outputs` is an untyped object. The outputs schema appeared only in the prompt, so a provider enforcing the schema let the model return `outputs: {}`. That happened in the live run: no files, no claims.
- **The fix.** Runtimes with an outputs model (Developer, QA) now ask for `<Runtime>Submission`, an `AgentSubmission` whose `outputs` field is that model (`ModelAgent.response_model`).
- **Normalising.** The parsed reply is converted back to a plain `AgentSubmission` before the platform's own checks.
- **Retry codes.** A reply whose outputs don't fit is now refused by the gateway, and the corrective retry names `SCHEMA_INVALID` instead of `OUTPUTS_INVALID`.

### Software project plans
- **The problem.** A live run sometimes omitted the `project` section, which is optional in the shared plan schema.
- **The fix.** Software projects are planned with `ProjectPlanProposal` (required `project`, `schema_version` 2), both in the prompt and as the provider schema (`plan.response_model`, prompt `software_plan/v3`).
- **Allowed skills.** Their tasks may only require the Developer's skills (`code_generation`, `implementation`, `debugging`), because only the Developer proposes files. A README task given to the Writer, or a test task given to QA, could never pass the file-scope check.
- **Visible stalls.** A READY task with no agent now counts as needing attention in progress (ADR-0053).
- **Required fields and conventions.** Every project task's `file_scope` and `effort_points` are required in the schema the model sees (`ProjectPlannedTask`, `software_plan/v4`). Stack profiles carry their layout `conventions` into the prompt (`software_plan/v5`).

### Verification and checks (from the live runs)
- **`deterministic/v3`.** A software project task's files are its deliverables (the text deliverable check is skipped). Extra tests outside its scope are a warning (`TEST_OUTSIDE_SCOPE`) and are never applied, so other tasks' files stay untouched.
- **Python profile checks** run pytest with `PYTHONPATH=src` and `--import-mode=importlib`, so the standard src layout and same-named test files in different folders work.

## Consequences
- **Gemini works.** Gemini accepts the analysis, plan and submission schemas; this was verified live.
- **More retries are possible.** A reply can break a bound the provider no longer saw, for example a skill name that doesn't match its slug pattern. It is then refused by validation and retried, exactly as with providers that ignore schemas. Prompts still state the rules (allowed skills, limits).
- **The test that pinned the exact schema sent to the provider** now expects `provider_schema(...)`. `tests/unit/test_provider_schema.py` checks that structure and names survive.
