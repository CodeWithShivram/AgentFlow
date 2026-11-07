# ADR-0005: Async SQLAlchemy with psycopg 3 in the API; selector event loop on Windows

- Status: Accepted
- Date: 2026-11-07
- Supersedes: handoff §3 "psycopg 3 synchronous PostgreSQL driver for the initial implementation" (API process only)

## Context
The handoff baseline was synchronous psycopg 3. The owner's Step 1.3 brief requires an SQLAlchemy 2.x **async** engine and session for the API, with async Redis. Celery workers (Step 1.8) are synchronous.

On Windows, Python's default asyncio loop is `ProactorEventLoop`. Async psycopg refuses to run on it. uvicorn 0.54 also picks the Proactor loop on Windows unless told otherwise.

## Decision
- API process: `create_async_engine("postgresql+psycopg://…")` with psycopg 3 in async mode, `async_sessionmaker`, and a request-scoped `get_db_session` dependency. Services own commits; the dependency only rolls back on error and closes.
- Keep psycopg 3 rather than asyncpg, so one driver serves both the async API and the synchronous Celery workers (which will use a sync engine on the same URL).
- On Windows, run uvicorn with `--loop asyncio:SelectorEventLoop`. Tests use the same loop via `TestClient(backend_options={"loop_factory": ...})` and the anyio `anyio_backend` fixture.
- The application lifespan fails fast with a clear message if it detects a Proactor loop on Windows, instead of letting every query fail later.

## Alternatives considered
- asyncpg: works on any loop, but adds a second PostgreSQL driver once sync workers exist, and departs from the handoff's driver choice.
- Stay synchronous: contradicts the owner's explicit Step 1.3 requirement.

## Consequences
- The Windows run command must include the `--loop` option (documented in README and CLAUDE.md). Linux/containers are unaffected.
- API repositories are written against `AsyncSession`. How synchronous Celery workers access the database (a sync engine on the same URL, or running async code inside tasks) is an open decision for Step 1.8. ORM models are shared either way.
