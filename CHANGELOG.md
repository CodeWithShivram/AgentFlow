# Changelog

All notable changes to AgentFlow. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-11-12 — Phase 1: Foundation

The first release: the platform's foundation, end to end. People can register, work in shared workspaces, manage agents, plan workflows as task graphs, and watch tasks run in the background. A **mock agent** executes the tasks: there is no LLM call yet, and that arrives in Phase 2.

### Added
- **Accounts and workspaces** ([ADR-0007](docs/ADR/0007-authentication-sessions-and-rbac.md)):
  - registration with a personal workspace; login, logout and session restore;
  - Argon2id passwords;
  - JWT access tokens and rotating refresh tokens, carried only in HttpOnly cookies, with reuse detection;
  - CSRF tokens and Origin checks;
  - rate limits that fail closed;
  - roles Owner, Admin, Member and Viewer; one-time invitation codes.
- **Tenant isolation in the database** ([ADR-0012](docs/ADR/0012-row-level-security.md)):
  - PostgreSQL row-level security on every workspace-owned table;
  - the API connects as a restricted role, scoped per transaction to the signed-in user.
- **Agent registry** ([ADR-0008](docs/ADR/0008-agent-registry-and-versioning.md)):
  - six built-in specialists;
  - workspace agents with editable drafts and immutable published versions; activation;
  - agent proposals with an independent review;
  - a tool and capability catalog with risk levels.
- **Workflows and tasks** ([ADR-0009](docs/ADR/0009-workflow-and-task-lifecycle.md)):
  - 10 workflow statuses and 14 task statuses, enforced by one transition engine;
  - plan approval; dependencies with cycle detection; agent assignment pinned to a version;
  - optimistic concurrency (`revision`);
  - a budget limit with currency;
  - an append-only event history; idempotent workflow creation.
- **Background execution** ([ADR-0010](docs/ADR/0010-background-execution-outbox-and-workers.md)):
  - Celery workers fed by a transactional outbox;
  - at-least-once delivery with deduplication;
  - a recorded history of every task attempt; bounded retries; hourly cleanup.
- **Web application** ([ADR-0011](docs/ADR/0011-frontend-application-architecture.md)):
  - a responsive dashboard, light and dark themes;
  - workflow planning with a dependency view, lifecycle actions and live monitoring (polling);
  - agents, versions and proposals; members and invitations;
  - accessible forms and dialogs.
- **Quality:**
  - 739 backend tests against real PostgreSQL and Redis;
  - 77 frontend unit and component tests;
  - 11 Playwright journeys against the real stack;
  - GitHub Actions workflows `backend-ci` and `frontend-ci`.

### Upgrade and setup notes
- **New required setting.** `API_DB_PASSWORD` must be set (see `.env.example`). After migrating, run `uv run python -m app.db.roles provision` to create the restricted API and worker roles; re-run it after every migration.
- **Administrator only.** `POSTGRES_USER` is now used only for migrations, seeding and provisioning.

### Known limitations
- **Mock agent.** Agents do not call a model yet. Tasks end at `SUBMITTED`; verification arrives in Phase 5.
- **Not measured yet.** Spend, token usage and notifications are not measured, and the UI says so.
- **Missing endpoints.** Member role changes, workspace rename and password change have no API endpoint yet. They are listed with proposed endpoints in `IMPLEMENTATION_PLAN.md`.
- **Before any hosted deployment:**
  - serve the frontend and API from one host (OD-19);
  - add a Content-Security-Policy (OD-19);
  - handle trusted proxy IPs (OD-14);
  - use a non-superuser schema owner (OD-20).
- **CI not yet run.** The GitHub Actions workflows were validated locally but have not run on GitHub yet: nothing has been pushed.
