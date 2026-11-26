# ADR-0047: Artifact storage

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.1 (was 8.3: artifact management); handoff §3 (local filesystem storage, metadata in PostgreSQL), §10 (`task_artifacts`), §13 (file type, size and path validation)
- Builds on: ADR-0012 (row-level security), ADR-0046 (Phase 9)

## Context
- **What needs it.** Step 9.6 packages verified projects as ZIP files, which must be downloadable only by members of the project's workspace.
- **What the handoff asks for:**
  - bytes on the local filesystem, outside source-controlled code;
  - metadata and checksums in PostgreSQL;
  - validated type, size and path.
- **What existed.** No storage. `tool_invocations.output_reference` was reserved for it.

## Decisions

### Storage (`app/artifacts/storage.py`)
- **`LocalArtifactStorage`.** It stores bytes under one root: `ARTIFACT_STORAGE_ROOT`, by default `~/.agentflow/artifacts`. Settings refuse relative roots and roots inside the repository.
- **Keys.** They are always `<workspace_id>/<artifact_id>`, built from UUIDs by the platform. A key is parsed again on every use and must be in canonical lower-case form.
  - The resolved path must stay inside the resolved root.
  - Symlinked or junctioned roots, workspace folders and files are refused.
  - Names chosen by people, agents or models never reach a path.
- **Writes.** Each write streams into a temporary file in the destination folder, then:
  - enforces `ARTIFACT_MAX_BYTES` (default 200 MiB) while copying;
  - hashes with SHA-256;
  - `fsync`s and renames atomically, refusing an existing key.

  Any failure removes the temporary file.
- **Reads.** A read checks it is a regular file, then checks the recorded size and SHA-256 before the bytes are served. A mismatch is `ARTIFACT_INTEGRITY_FAILED` (409); a missing file is `ARTIFACT_UNAVAILABLE` (410).
- **Errors.** They carry fixed codes and never include paths.

### Records (`artifacts` table, migration 0030)
- **Columns:**
  - `workspace_id` and `workflow_id` (composite FK), optional `task_id` (composite FK) and `execution_id`;
  - `kind`: PROJECT_ARCHIVE, BUILD_REPORT or TASK_OUTPUT;
  - a safe `filename` (letters, digits, `.`, `_` and `-`; no leading dot; at most 120 characters);
  - a `media_type` fixed by extension (unknown types are `application/octet-stream`, never HTML or SVG);
  - a unique `storage_key`, `size_bytes`, `sha256`;
  - `status` (AVAILABLE or DELETED) with `deleted_at` and `deleted_by_user_id`.
- This is the handoff's `task_artifacts`, named `artifacts` because project archives belong to a workflow rather than a task.
- **A trigger** refuses any change to an artifact's identity (key, size, checksum, links, name, type, kind). It allows only AVAILABLE → DELETED, once.
- **Row-level security:** tenant and platform policies, like every workspace-owned table. Workers get SELECT, INSERT and UPDATE.

### Who creates, reads and deletes
- **Creating.** Only platform code (worker jobs) creates artifacts, through `app.artifacts.records.store_artifact`. It stores the bytes, then flushes the row in the caller's transaction, and removes the bytes if the insert fails. There is **no upload endpoint**.
- **API.** Under `/workspaces/{id}/workflows/{workflow_id}/artifacts`:
  - list and get metadata (VIEWER; the storage key is never returned);
  - `GET …/{artifact_id}/download` (VIEWER), served only after verification as an attachment with `nosniff`, `Cache-Control: private, no-store`, a sandboxing CSP and `X-Checksum-SHA256`;
  - `DELETE …/{artifact_id}` (ADMIN): marks the row DELETED and commits, then removes the bytes.
- **Scoping.** Lookups go through the workflow scoped to the caller's workspace, so non-members get 404. Row-level security is the second layer.

## Consequences
- **Downloads are safe to serve.** Packaging (9.6) can store archives and reports, and downloads already enforce scoping and integrity.
- **Orphaned files.** A crash between writing the bytes and committing the row, or a failed removal after a deletion, leaves a file no download can reach (downloads start from an AVAILABLE row). An orphan sweep, which would compare files with rows, is not built yet. It is tracked for Step 8.6 (backup/restore and operations).
- **Verification cost.** Each download reads the file twice (once to verify, once to serve). That is acceptable at the size limit for a local MVP.
- **Retention.** Retention is manual (ADMIN delete). Automatic retention policies are for a later step.
- **Hosted deployments.** They need shared storage. The interface (`put`, `verified_path`, `delete`) is the seam for an object-store implementation.
