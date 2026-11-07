# ADR-0008: Agent registry, immutable versions and dynamic proposals

- Status: Accepted
- Date: 2026-11-07
- Amends: ADR-0006 (agent status values become `ACTIVE` / `INACTIVE`)

## Context
Step 1.6 adds the registry of specialist agents. Requirements:
- predefined specialists and workspace-specific agents;
- immutable published versions, and executions that keep the exact version they started with;
- dynamic agent proposals that need explicit, authorized approval;
- strict configuration validation, no stored credentials, and no privilege escalation through configuration.

The registry stores and validates configuration only. It never runs instructions or tools.

## Decisions

### Configuration (schema version 1)
`AgentConfiguration` is a strict, frozen Pydantic model that rejects unknown fields at every level:

| Field | Rule |
|---|---|
| `system_instruction_ref` | A reference such as `builtin://researcher/v1`, never instruction text |
| `model` | A gateway *profile* name plus temperature and output limit, resolved from environment configuration in Phase 2. `credential_ref` may only be `secret://<name>`; raw keys are impossible |
| `capabilities` | Must exist in the platform catalog (`app/agents/catalog.py`) |
| `tools` | Each tool must exist in the catalog |
| other | input/output schema versions, timeout and token limits |

- Every text field is scanned for credential-shaped values (`sk-…`, `AKIA…`, `ghp_…`, PEM private keys, bearer tokens). Validation errors never echo the value.
- **The catalog is platform policy.** Tools carry a risk level:
  - LOW: workspace artifacts only;
  - MEDIUM: external network;
  - HIGH: executes code (`python_analysis`, `test_runner`). HIGH tools are marked unavailable in Phase 1. Declaring one records a permission only; the tool gateway (Phase 3) enforces availability at run time.

### Lifecycle and versioning
- **`agents`** holds the definition, an editable `draft_configuration`, `status` (`ACTIVE` / `INACTIVE`), `active_version_id` and a `revision` counter. A CHECK requires a published version for `ACTIVE`.
- **Publishing** re-validates the draft against the current schema and catalog, then inserts `agent_versions` row N+1 and points `active_version_id` at it. Publishing an unchanged draft is rejected (409 `NOTHING_TO_PUBLISH`).
- **`agent_versions` is immutable.** A trigger rejects every `UPDATE`.
- **Tasks pin their version:** `workflow_tasks.assigned_agent_version_id` is pinned at assignment.
  - A composite FK `(assigned_agent_id, assigned_agent_version_id) → agent_versions(agent_id, id)` guarantees the version belongs to the agent.
  - `ON DELETE RESTRICT` keeps referenced versions forever, and a CHECK requires agent and version to be set together.
  - `resolve_runnable_version()` returns the active version for new assignments (used in Step 1.7). Publishing later never changes what an existing task runs.
- **Optimistic concurrency:** every mutation names the `revision` it read. The row is locked (`FOR UPDATE`) and compared; a mismatch returns 409 `AGENT_REVISION_CONFLICT` with the current revision. Every successful change increments it.

### Built-in specialists
- Researcher, Analyst, Developer, Writer, QA and General are defined in `app/agents/builtin_agents.json`. They are validated with the same schema and loaded by `python -m app.agents.seed`, which is idempotent: it creates missing agents, publishes a new version for a changed definition, and leaves unchanged ones alone.
- Built-ins have `workspace_id = NULL`, are visible to every workspace, and are **read-only** to workspaces (403 `AGENT_READ_ONLY`).

### Authorization
| Action | Minimum role |
|---|---|
| List or read agents, versions and proposals | VIEWER |
| Submit a proposal | MEMBER |
| Create an agent, edit its draft, publish, activate or deactivate | ADMIN |
| Approve or reject a proposal | ADMIN, and not the proposal's author (403 `PROPOSAL_SELF_REVIEW`; also a DB CHECK) |
| Publish, approve or activate a configuration granting a HIGH-risk tool | OWNER (403 `TOOL_REQUIRES_OWNER`) |

- Roles come from `workspace_role()` and `WorkspaceAccess` (ADR-0007).
- Agents of other workspaces are invisible: 404 `AGENT_NOT_FOUND`, the same as for a random ID. Proposals are likewise scoped to their workspace.

### Dynamic proposals
- **Storage:** `agent_proposals` is separate from agents and holds the proposed configuration, rationale, proposer, status (`PENDING` / `APPROVED` / `REJECTED`), reviewer, review time, note and the resulting agent.
- **Frozen content:** a trigger prevents the proposed content from changing after submission, and prevents a decided proposal from changing at all.
- **Approval:**
  1. Re-validates the frozen configuration against the current catalog and the reviewer's authority.
  2. Registers a new workspace agent whose immutable version 1 is exactly that configuration.
  3. **Leaves the agent `INACTIVE`.** Activation is a separate authorized action, in line with handoff §7: "Agent proposals are not automatically active".
- **No self-escalation:** a proposal can only create a new agent, never change an existing one. Its schema has no status, decision or permission-override fields, and it can't approve itself.
- `requested_by` may be NULL for future planner-generated proposals (Phase 3).

### Audit
`agent_audit_events` is append-only (a trigger rejects `UPDATE`). It records:
- `CREATED`, `DRAFT_UPDATED`, `VERSION_PUBLISHED`, `ACTIVATED`, `DEACTIVATED`;
- `PROPOSAL_SUBMITTED`, `PROPOSAL_APPROVED`, `PROPOSAL_REJECTED`.

Each event carries the actor (NULL for platform seeding), the workspace (NULL for built-ins) and small details such as the version number. Events are written in the same transaction as the change. There is no read API yet; it will come with the frontend or analytics work.

## Alternatives considered
- **Editing versions in place:** rejected, because it breaks reproducibility of past executions.
- **Approval that also activates:** rejected; the handoff requires proposals not to be automatically active.
- **Free-form configuration JSON:** rejected, because unknown keys could carry credentials or pseudo-permissions.
- **Instruction text stored in the configuration:** deferred. A reference keeps prompts versioned separately and out of the permission surface.

## Consequences
- Every edit requires the client to send the current `revision`.
- Adding a tool or capability is a code change to the catalog, reviewed like any other policy change.
- Changing a built-in requires editing `builtin_agents.json` and re-running the seed command, which produces a new version.
