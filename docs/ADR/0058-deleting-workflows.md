# ADR-0058: Deleting workflows (soft delete with file purge)

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.11; the owner asked for "an option to delete the projects"

## Context
- **Projects pile up.** Failed experiments and stuck runs (for example the first live mini-shop run) stay on the Projects page and keep their Git workspace and downloads on disk.
- **A hard delete would break invariants.** `workflow_events`, `verification_results`, `agent_selections`, `approval_decisions` and `llm_usage` are append-only history. Spend must stay in analytics, and budgets and audits must not change after the fact.

## Decision
- **Soft delete.** `DELETE /workspaces/{id}/workflows/{workflow_id}?revision=N` (ADMIN) sets `workflows.deleted_at` and `deleted_by_user_id` (migration 0037). It also bumps `revision`, records a `WORKFLOW_DELETED` event (`status` category) and writes a `workflow.purge` outbox row, all in one transaction.
- **Only idle workflows:** PENDING, COMPLETED, FAILED or CANCELLED. Anything else returns 409 `WORKFLOW_ACTIVE` with the status. A check constraint (`deleted_only_when_idle`) enforces the same rule for any write. The project page offers **Cancel & delete** for active projects: a cancel transition followed by the delete, behind one confirmation.
- **Gone for the API.** The workflow repository's list, get and lock exclude deleted rows, so every workflow route returns 404, including tasks, events, packages and artifacts, which all resolve the workflow first. The interventions inbox hides a deleted workflow's requests; the approval audit log keeps them.
- **History stays.** Analytics, metrics and the approval audit still count a deleted workflow's records, because they describe spend and decisions that happened.
- **Files are purged by a worker** (`app/projects/purge_job.py`, idempotent via the inbox):
  1. mark the workflow's AVAILABLE artifacts DELETED and commit;
  2. delete their bytes;
  3. remove the project workspace (`ProjectWorkspaces.remove`, which refuses links like every workspace path and makes Git's read-only files writable).

  Failures leave unreachable files for the orphan sweep (ADR-0047). The package job ignores deleted workflows.

## Consequences
- **Deleting cannot be undone from the UI.** The rows remain, so an operator could restore the metadata, but the files are gone.
- **Workers keep running their claims as before.** A deleted workflow is terminal or never started, so no task, plan or integration job acts on it.
- **Tests:** `tests/integration/test_workflow_deletion.py` covers the API (admin only, revision, idle only, hidden afterwards, history kept), the database constraint and the purge job. A frontend test covers Cancel & delete.
