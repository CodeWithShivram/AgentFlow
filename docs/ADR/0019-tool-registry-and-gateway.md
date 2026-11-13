# ADR-0019: Tool registry and server-side tool gateway

- Status: Accepted
- Date: 2026-11-13
- Implements: Phase 3, Step 3.2 (tool registry and server-side permission enforcement); handoff §13 ("explicit per-agent tool permission manifest", "enforce permissions in a server-side tool gateway"), §11 `tool_invocations`
- Builds on: ADR-0008 (catalog and tool permissions), ADR-0013 (gateway and usage-record pattern), ADR-0018 (task contract)
- Owner decisions (2026-11-13):
  - no tool gets a working implementation yet;
  - calling a tool that isn't allowed fails the attempt;
  - tool calls are exposed through the API only, with no UI yet.

## Context
- **Policy without enforcement.** Agent versions are granted catalog tools with read or write access (ADR-0008), but nothing enforced those grants at run time.
- **Nothing to run yet.** Workspace files and artifacts need storage (Step 8.3), web search needs a provider choice and credentials, and code execution stays disabled (handoff §13).

## Decisions

### Registry (`app/tools/registry.py`)
- **Contents.** Each catalog tool has one `ToolDefinition`: the access it requires, and an optional `ToolImplementation`. An implementation has strict Pydantic argument and result models, a handler, and a result size limit (20,000 characters by default).
- **Required access:**
  - read: `document_retrieval`, `workspace_files_read`, `web_search`;
  - write: `artifact_writer`, `workspace_files_write`, `python_analysis`, `test_runner`.
- **Availability.** A tool is available only when it has an implementation and the catalog enables it. No tool has an implementation, so none is available: nothing is faked, and no provider, dependency or setting is added.
- **Usable grants.** `usable_grants` keeps a version's grants that are available and give enough access; write access covers read.

### Contract
- **Shape.** `TaskContract.allowed_tools` is now a list of `ToolGrant` entries (`{tool, access}`) holding exactly the usable grants. Contracts aren't persisted, so changing their shape is safe. Today every contract lists no tools.

### Gateway (`app/tools/gateway.py`)
- **Construction.** One `ToolGateway` per attempt, reachable only as `AgentContext.tools`. `invoke(name, arguments)` checks, in order:
  1. **Call limit.** At most 50 calls per attempt; more is `TOOL_CALL_LIMIT_EXCEEDED`.
  2. **Permission.** The tool must be in the contract, known, available, and covered by its grant's access. Anything else is `TOOL_PERMISSION_DENIED`, including unknown names.
  3. **Arguments.** They must validate against the tool's schema, or the call fails with `TOOL_ARGUMENTS_INVALID`.
  4. **Handler.** It runs with a `ToolCallContext` (workspace, workflow, task, execution) that it must scope everything to. Any exception becomes `TOOL_FAILED`; only its type is logged.
  5. **Result.** It must validate against the result model and fit the size limit, or the call fails with `TOOL_OUTPUT_INVALID`.
- **Violations.** Steps 1 and 2 are policy violations. The gateway keeps the first one. `run_agent` raises it after the agent returns or fails, even if the agent caught it, so a probing agent can't hide it. The worker records the attempt as FAILED with the error code and a fixed message, and the usual REWORK_REQUIRED / FAILED path follows.
- **Other errors.** Errors from steps 3–5 are not violations. An agent may handle them; an unhandled one fails the attempt with its own code.
- **Error text.** Every error has a fixed, safe message. Neither the message nor the record contains arguments, tool internals or output, and exception chains are cut (`from None`).
- **Results are untrusted data.** Results reach the agent as validated models: data, never instructions (handoff §13).

### Invocation records (migration `0012_tool_invocations`)
- **Columns.** `tool_invocations` follows handoff §11: execution, tool name (as called, truncated to 100 characters), status (`SUCCEEDED` / `DENIED` / `FAILED`), error code, duration in ms, input hash and output reference. It also has workspace, workflow and task for scoping, with composite foreign keys.
- **What's stored.** Only the SHA-256 of the canonical JSON arguments: never the arguments or output. `output_reference` stays NULL until artifact storage exists.
- **Transactions.** Each call is written in its own transaction by `record_tool_invocation`, like `llm_usage`, so a call is recorded even when its attempt later fails.
- **Constraints:**
  - the error code is set exactly when the call did not succeed;
  - the duration is ≥ 0;
  - the hash is 64 lowercase hex characters.
- **RLS.** The table has tenant and platform RLS policies (ADR-0012). The worker gets SELECT and INSERT.
- **API.** `GET …/tasks/{id}/executions` now includes each attempt's `tool_invocations`, oldest first. There is no UI until a tool can actually run.

## Consequences
- **Adding a tool** is a registry entry with schemas and a handler, plus the catalog policy. The contract, gateway, records and violation handling apply automatically.
- **Where enforcement lives.** It is server-side and independent of prompts (handoff §13). An agent can't reach a tool without the gateway, because it never receives database sessions, credentials or handlers.
- **Not covered yet.** Approval for sensitive external actions (handoff §9, MEDIUM-risk tools) belongs with human intervention (Phase 6), before any external tool is implemented. Per-call costs for tools are Phase 7.
