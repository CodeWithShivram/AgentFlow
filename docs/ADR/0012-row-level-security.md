# ADR-0012: Row-level security enabled

- Status: Accepted
- Date: 2026-11-12
- Resolves: OD-13 (enable PostgreSQL row-level security), OD-15 (case-insensitive agent names), OD-10 (`httpx2`)
- Implements: the RLS design in ADR-0007, with the deviations listed below
- Opens: OD-20 (non-superuser schema owner for hosted deployments)

## Context
Until now the API connected as `POSTGRES_USER`. That role is a superuser that owns every table, so PostgreSQL skipped row-level security for it. Tenant isolation rested only on application code:
- `workspace_role` authorization;
- repositories that filter by `workspace_id`;
- composite foreign keys.

The owner scheduled OD-13 before the Phase 1 release, so that a missing filter can no longer leak another workspace's data.

## Decisions

### Roles
| Role | Used by | Attributes | Row-level security |
|---|---|---|---|
| `POSTGRES_USER` | Alembic, built-in seeding, role provisioning | administrator, schema owner | bypassed (superuser) |
| `agentflow_api` (`API_DB_USER`) | the API | `NOSUPERUSER NOBYPASSRLS NOINHERIT`, owns nothing | member of `agentflow_rls_tenant` |
| `agentflow_worker` (`WORKER_DB_USER`) | Celery workers | as ADR-0010 | member of `agentflow_rls_platform` |

- **Group roles.** `agentflow_rls_tenant` and `agentflow_rls_platform` are NOLOGIN group roles that hold **no privileges**.
  - Policies name these groups, so the login role names stay configurable (tests use their own roles).
  - Membership is granted `WITH INHERIT TRUE`, so the policies apply although the login roles are `NOINHERIT`.
  - A login role in neither group sees no rows of a secured table.
- **API privileges.**
  - The API role has `SELECT, INSERT, UPDATE, DELETE` on application tables, and sequence usage.
  - It has no rights on `alembic_version` or the worker-only `inbox_messages`.
- **Provisioning.** `uv run python -m app.db.roles provision` provisions both roles and is idempotent. It must run after every migration that adds tables.
- **Credentials.** The API refuses to start without `API_DB_PASSWORD`, so it can never fall back to the superuser silently.

### Per-transaction user context
- **How the context is set.**
  - The API's `AsyncSession` uses `TenantSession` (`app/db/session.py`).
  - `set_session_user(session, user_id)` stores the user in `session.info` and runs `set_config('app.user_id', :id, true)` immediately.
  - An `after_begin` listener repeats the setting at the start of every later transaction of that session.
- **When it is set.**
  - Authentication sets it as soon as the principal is known.
  - Registration sets it after creating the user.
  - The login, refresh and register responses set it before reading memberships.
- **Why it cannot leak.** `is_local = true` limits the value to the current transaction. It disappears at COMMIT or ROLLBACK and never reaches the next user of a pooled connection; this is tested.
- **Without a user**, the policies match no workspace rows.

### Policies (migration `0006`)
- **Workspace-owned tables** have `ENABLE` and `FORCE ROW LEVEL SECURITY`:
  - `workspaces`, `workspace_members`;
  - `workflows`, `workflow_tasks`, `task_dependencies`, `workflow_events`;
  - `agents`, `agent_versions`, `agent_proposals`, `agent_audit_events`;
  - `idempotency_keys`, `outbox_events`, `agent_executions`.
- **Tenant policy:**
  - It is `workspace_id IN (SELECT app_user_workspace_ids())`.
  - For `task_dependencies` and `agent_versions`, which have no `workspace_id`, the condition goes through the parent table.
  - `app_user_workspace_ids()` is `STABLE SECURITY DEFINER` with a pinned `search_path`, so policies never recurse into `workspace_members`.
- **Exceptions to the tenant policy:**
  - **`workspaces`**: the owner also sees a workspace before its OWNER membership exists (registration).
  - **`workspace_members`**: users always see their own membership rows. They may insert their own membership only when `app_can_join_workspace(workspace_id)` holds: they own the workspace, or an unexpired invitation is addressed to their email.
  - **Built-in agents and their versions** (`workspace_id IS NULL`) are readable by every tenant and writable by none. Only the administrator seeds them.
- **Platform policy** (workers): `USING (true)`. The worker remains limited by its table grants (ADR-0010).
- **Tables outside RLS**, as ADR-0007 designed, are guarded by the service layer:
  - `users`, `auth_sessions`, `refresh_tokens`: used by login and refresh, which run before any user context exists;
  - `workspace_invitations`: looked up by token before membership exists;
  - `inbox_messages`: worker-only.

### OD-15 and OD-10
- **OD-15.** The `(workspace_id, name)` unique constraint became the unique index `uq_agents_workspace_id_lower_name` on `(workspace_id, lower(name)) NULLS NOT DISTINCT`. A concurrent case-variant duplicate is now rejected by the database and mapped to `AGENT_NAME_TAKEN`.
- **OD-10.** Tests use `httpx2`, which Starlette's `TestClient` now prefers; the deprecation warning is gone.

## Deviations from the ADR-0007 design
1. **Schema owner.**
   - ADR-0007 planned a non-superuser `agentflow_owner` to own the schema.
   - Here the administrator (`POSTGRES_USER`, a superuser in the local Docker image) still owns it and runs migrations. Applications never connect as it.
   - The security gain comes from the API role, which is now restricted and subject to RLS.
   - Moving ownership to a non-superuser role is tracked as **OD-20**, before any hosted deployment.
2. **Workers.**
   - ADR-0007 suggested workers set a per-task workspace context.
   - The outbox relay and cleanup are cross-tenant by nature, so the worker's policy is unscoped. Its table grants and the absence of `users` access still restrict it.
   - Per-task scoping of the execute path can follow with the model gateway (Phase 2+).
3. **Context storage.** The user is kept on `session.info` (request-scoped) rather than in a contextvar. It is explicit, tied to the session that runs the queries, and cannot cross requests.

## Consequences
- **Two layers of isolation.** A repository query that forgets `workspace_id` now returns nothing from other workspaces, instead of leaking them. Application filters and `workspace_role` remain the first layer.
- **Test coverage.** Every API integration test runs as a provisioned restricted role, so the whole suite exercises RLS. `tests/integration/test_row_level_security.py` checks isolation with raw unfiltered SQL.
- **Operations.**
  - New environment variable `API_DB_PASSWORD`.
  - `app.db.roles provision` after migrations.
  - Seeding runs as the administrator.
