# ADR-0052: Deliverable packaging

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.6; ADR-0046 decision 6; playbook §9 (deliverable packaging)
- Builds on: ADR-0047 (artifact storage), ADR-0049 (workspaces), ADR-0051 (integration gate)

## Context
- **What users get.** The ZIP of a coherent, verified project is the product's deliverable.
- **Rules from playbook §9:**
  - include only what belongs to the project;
  - never include secrets;
  - add a manifest with checksums and a build report;
  - validate the archive after writing;
  - serve it only to authorised users;
  - label anything unverified as PARTIAL.

## Decisions

### When packages are made
- **Automatically.** A passing integration completes the workflow and requests `project.package` in the same transaction.
- **On request.** An ADMIN may request a package with `POST …/workflows/{id}/packages`, but only for a software project that is BLOCKED, PAUSED or COMPLETED.
- **Labels.** The label comes from stored evidence, not from the request:
  - **VERIFIED** only when the workspace's current commit is the commit of the latest PASSED integration;
  - **PARTIAL** otherwise, and then only when an admin asked for it (`requested_by_user_id` is recorded).
- **What an automatic request needs.** A COMPLETED, verified project. Anything else does nothing (`stale`).
- **Duplicates.** A READY package of the same commit and label makes further requests duplicates.

### What goes in (`app/projects/packaging.py`)
- **Candidates.** Only Git-tracked files at the packaged commit, which are only platform-written files (ADR-0049).
- **Re-checks:**
  - paths must be relative, without traversal or backslashes;
  - links and junctions are refused;
  - the resolved path must stay in the project.
- **Excluded and counted (the denylist):**
  - real `.env` files (`.env.example` is kept);
  - keys and certificates;
  - local databases;
  - logs and bytecode;
  - VCS, dependency, cache and build folders;
  - credential files.

  Project files that would collide with the generated report or manifest are excluded too.
- **Secret scan.** Private-key headers, AWS keys, provider keys (`sk-…`), GitHub and Slack tokens, Google API keys, and URLs with credentials. Placeholders such as `change-me`, `<password>` or `user:password@` don't count. Any hit refuses the whole package: FAILED with `SECRET_DETECTED` and a count, never the path or the value.
- **Limits.** 2,000 files, 2 MiB per file, 50 MiB in total.

### How it is built
- **Layout.** One top-level folder (the project's safe name), fixed timestamps and permissions, and sorted entries, so the same commit gives the same bytes.
- **Added files:**
  - `AGENTFLOW_BUILD_REPORT.md`: label, stack, commit, architecture, assumptions, setup and check commands, a task table and check outcomes. Check output is not copied in. A PARTIAL report says so prominently.
  - `AGENTFLOW_MANIFEST.json`: path, size and SHA-256 per file.
- **Verification after writing.** The archive is re-opened and checked: CRCs, only names under the folder, no unsafe names, no duplicates, and every file's SHA-256.
- **Storage.** Through artifact storage (ADR-0047): the archive as PROJECT_ARCHIVE (`<name>-<commit>[-partial].zip`) and the report as BUILD_REPORT. Both rows sit in a savepoint, so a failure stores neither rows nor bytes.
- **Records.** `project_packages` (migration 0034): one append-only row per attempt, READY with both artifacts or FAILED with a code; RLS. Workers SELECT and INSERT.

### Downloads
- **Listing.** `GET …/workflows/{id}/packages` (VIEWER) lists packages with their artifact ids.
- **Downloading.** Through the existing artifacts API, which is workspace-scoped and verifies checksums (ADR-0047).
- **Workspace kept.** The workspace stays, so a project can be continued, re-checked and re-packaged.

### Also fixed
- `artifacts.deleted_by_user_id` is now `ON DELETE RESTRICT`. The artifacts trigger refuses updates to deleted rows, so the previous `SET NULL` could never succeed.

## Consequences
- **The deliverable promise.** A VERIFIED download always corresponds to a commit whose checks passed in the sandbox, with a manifest proving its contents.
- **PARTIAL packages** exist only by an admin's explicit choice and say so in the file name and report.
- **Pattern-based secret scanning** can miss unusual formats. No real secrets ever enter agent context, workspaces or the sandbox, so the scan is a second layer.
