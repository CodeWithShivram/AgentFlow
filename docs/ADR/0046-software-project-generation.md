# ADR-0046: Software project generation (Phase 9 direction)

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.0 (rebaseline against `docs/AgentFlow_Claude_Code_Master_Playbook.txt`)
- Supersedes in part: ADR-0022 ("Developer and QA agents without execution") once Steps 9.3–9.5 land
- Owner decision (2026-11-26): approve the Phase 9 sequence below, which turns AgentFlow into a prompt-to-tested-project product

## Context
- **Where the repo stands.** Phases 1–7 and Steps 8.1–8.2 are done. A prompt becomes an analysed, planned and confirmed task graph. Specialist agents run it with verification, retries, interventions, budgets and reports.
- **The product goal.** The playbook asks for more: describe software, approve a plan, download a tested, documented project as a ZIP.
- **The gap.** No file is ever written, run, integrated or packaged:
  - the Developer agent returns path-checked `proposed_files` that nothing applies;
  - QA designs tests that nothing runs;
  - artifact storage (Step 8.3) doesn't exist.
- **The playbook's roadmap.** Its Steps 1.9–2.8 are mostly done under other numbers. The mapping is in `IMPLEMENTATION_PLAN.md` § Phase 9.

## Decisions

### 1. Keep the numbering; add Phase 9
- Completed steps keep their numbers.
- The remaining product work is Phase 9.
- Step 8.3 (artifact storage) becomes Step 9.1, because packaging depends on it.
- Steps 8.4–8.7 follow Phase 9 unchanged.

### 2. A project is a workflow kind, not a new aggregate
- `workflows.kind` (`general` | `software_project`) and a software plan schema (v2: stack profile, file ownership, effort) are added in Step 9.2.
- The workflow, its tasks, attempts, verification, interventions, usage and events stay the single source of truth.
- Nothing is duplicated.

### 3. Agents still propose; the platform writes
- Agents never get a writable filesystem, a shell or network access.
- The Developer agent keeps returning structured `proposed_files`.
- After a task's proposal passes verification, the **platform** (a worker job) applies it to the project workspace.
- One serial applier works per project, in dependency order, with:
  - the existing path checks;
  - per-task file ownership from the plan.
- A write outside a task's ownership, or to a path another task owns, is a verification failure that goes through the existing rework policy (ADR-0032).
- The workspace is a local Git repository per project. Each applied task is one commit by the platform. Hooks are disabled, and the platform alone writes `.git`.
- So no two agents ever share a working tree: agents share none at all.

### 4. Code runs only in a Docker sandbox, and fails closed
- Generated code (tests, lint, build) runs only through a platform sandbox runner:
  - one short-lived container per run, non-root;
  - `--network none`, `--read-only` with a size-limited tmpfs;
  - `--cap-drop ALL`, `no-new-privileges`;
  - CPU, memory, process and wall-clock limits;
  - output capped and redacted;
  - only a copy of the project tree mounted.
- **Nothing else is mounted:** no home directory, Docker socket, credentials or other repositories. No secrets are passed in.
- **Stack profiles.** Dependencies come from prebuilt, pinned **stack profile images** (initially one Python and one Node/TypeScript profile). No install step runs with network access in the MVP. Plans must choose a supported profile and stay within its dependency set.
- **Without Docker,** the runner fails closed. Projects then end as PARTIAL with the reason, never "verified". There is no host fallback.
- **Its limits.** Docker is one layer, not a security guarantee. Step 9.4's ADR records the threat model and its limits.

### 5. Verification gates completion
- Per-task sandbox checks join deterministic and semantic verification as evidence.
- A project-level **integration gate** (all checks on the composed tree) must pass before the workflow may complete and before a deliverable is labelled verified.
- Failures create targeted rework within the existing bounded retry policies.
- Model self-reports never replace executed checks.

### 6. Deliverables
- The ZIP is built only from the workspace's tracked tree, with:
  - an allowlist/denylist;
  - path and symlink containment;
  - a secret scan;
  - size and file-count limits;
  - a manifest with checksums;
  - a generated README section and `AGENTFLOW_BUILD_REPORT.md`.
- It is re-opened and validated after writing.
- Metadata and checksum go to PostgreSQL (RLS). Files go to local artifact storage outside the repository.
- Downloads are workspace-scoped.
- A project whose gates failed can only produce an explicitly **PARTIAL** artifact.

### 7. Simple UX on top of the existing app
- "New project", "Projects", project detail (phase, weighted progress, a time-remaining range with a confidence level, agent activity, issues, deliverable) and downloads.
- Existing workflow, agent and analytics pages remain as the advanced view.

## Phase 9 sequence
| Step | Scope |
|---|---|
| 9.0 | This ADR; plan and progress updated (docs only) |
| 9.1 | Artifact storage (was 8.3): storage interface, local root, checksums, RLS-scoped metadata, download API |
| 9.2 | Software project kind and plan schema v2 (stack profile, file ownership, effort) |
| 9.3 | Project workspace: the platform applies verified proposals in dependency order with ownership checks |
| 9.4 | Docker sandbox runner and stack profile images (fail closed, threat model) |
| 9.5 | Execution checks and the integration gate feeding verification and rework |
| 9.6 | Secure ZIP packaging, README and build report, download |
| 9.7 | Project UX, weighted progress and time-remaining range |
| 9.8 | End-to-end prompt → ZIP journey on the mock provider; release v0.2.0 |

## Consequences
- **ADR-0022's "never write, never execute" changes.** Writing becomes a platform action, and execution becomes a sandboxed platform action. The agents themselves still neither write nor execute.
- **Docker becomes a runtime dependency** for verified software deliverables, not only for local infrastructure.
- **Unsupported stacks are refused at planning time.** The curated stack profiles limit what can be generated to stacks that can actually be verified.
- **Open items still apply:** OD-14, OD-19 and OD-20 before any hosted deployment. The live provider path is still unverified.
