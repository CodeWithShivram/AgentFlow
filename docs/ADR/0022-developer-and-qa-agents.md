# ADR-0022: Developer and QA agents without execution

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 3, Step 3.5 (Developer and QA agents, no unsafe shell or code execution); handoff §7 (Developer, QA), §13 (no unrestricted shell, execution disabled)
- Builds on: ADR-0019 (tool gateway), ADR-0021 (built-in runtimes)
- Owner decisions (2026-11-14):
  - the Developer agent delivers structured, path-checked file proposals;
  - the QA agent designs tests and reviews only.

## Context
- **The handoff.** It gives the Developer agent a controlled workspace and isolated test runner, and the QA agent an approved test runner, "only when enabled".
- **None of it exists.** Workspace files need artifact storage (Step 8.3), and the test runner is a HIGH-risk, disabled tool. Code execution stays disabled.
- **The gap.** Until Step 3.5, both built-ins failed with `AGENT_RUNTIME_UNAVAILABLE`.

## Decisions

### Runtimes
- **What they are.** `DeveloperAgent` (`builtin://developer/v1`, purpose `agent_development`) and `QAAgent` (`builtin://qa/v1`, purpose `agent_qa`) are model-backed runtimes on the ADR-0021 base. They add no shell, process, file or network access: the only thing they reach is the model gateway.
- **Instructions.** Both instruction texts state that nothing is executed or written, and forbid claiming that code compiled or ran, tests passed or failed, or files changed. A criterion that needs execution to confirm is at most PARTIAL (designed or proposed, not run), with that reason as evidence.
- **Platform notes.** A note is appended to every submission, whatever the model wrote, using the same mechanism as the Research note:
  - **Developer:** no code was executed and no files were written; proposals and tests are unverified.
  - **QA:** no tests were executed; test cases are designs only, and no results were observed.

  A note disappears only when the contract grants the matching tool: `workspace_files_write`/`test_runner` for the Developer, `test_runner` for QA. No contract grants either today.

### Structured outputs (`app/agents/outputs.py`)
- **Where they live.** `AgentSubmission.outputs` stays a free object. Each runtime declares a stricter `outputs_model`, which is sent to the model as a second JSON Schema and checked before a reply is accepted.
- **Retry.** A reply that doesn't fit gets the ADR-0021 corrective retry, with `OUTPUTS_INVALID` (or `TEST_CASE_UNKNOWN_CRITERION`). A second misfit fails the attempt as `INVALID_SUBMISSION`, and nothing is stored.
- **Developer outputs.** `proposed_files` (1–50) and `proposed_tests` (0–50), each `{path, language, purpose, content ≤ 50,000 chars}`, plus one text entry per deliverable. Paths are unique across both lists. Path checks:
  - relative POSIX paths only;
  - no `..`, `.` or empty segments, absolute paths, drive letters, backslashes, `:` (alternate data streams) or spaces;
  - at most 12 segments;
  - never `.git`, `.ssh`, `.aws`, `.azure`, `.kube`, `.docker` or `.gnupg` folders, nor `.env`/`.env.*` files (except `.env.example`).

  The paths are validated now, so they're safe to apply once workspace tools exist. Nothing writes them today.
- **QA outputs.** `test_cases` (1–100), each `{id, title, covers (criterion ids), kind manual|automated, steps, expected}`, plus deliverable entries. Ids are unique, and covered criteria must exist in the contract.
- **Mock defaults.** Deterministic mock replies produce valid proposals and one test case per criterion.

### UI
- **Executions panel.** It shows "Proposed files (not applied)" and "Proposed tests (not run)" as collapsible paths with their purpose and content in a `<pre>`, and "Test cases (designed, not run)" with steps, expected result and the covered criteria's text.
- **Untrusted data.** Outputs are parsed defensively (incomplete entries are skipped) and rendered as plain text only.

## Consequences
- **All six built-in agents now run.** Only workspace agents with their own instruction references still fail with `AGENT_RUNTIME_UNAVAILABLE`.
- **Verification.** Developer and QA results are proposals and designs that a person or platform verification (Phase 5) must check. The handoff's rule applies: never claim software is correct because a model says so.
- **Later work.** When a controlled workspace and an isolated test runner exist, the proposals can be applied and the tests executed through the tool gateway, in a later, separately approved step. The platform notes then disappear for contracts that grant those tools.
