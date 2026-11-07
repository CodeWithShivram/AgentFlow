# ADR-0001: Modular monolith with separate API and worker processes

- Status: Accepted
- Date: 2026-11-07

## Context
AgentFlow needs an HTTP API, long-running background agent work and a web UI. The MVP is local-first, built by a small team, and must stay cloud-ready without the cost of distributed services.

## Decision
One Git repository with `backend/` (FastAPI + Celery, one Python codebase), `frontend/` (Next.js) and `infrastructure/`. The API and the Celery worker run as separate processes from the same backend package. No microservices, Kubernetes or multi-region deployment in the MVP.

## Alternatives considered
- Microservices per agent/service: operational overhead with no MVP benefit; explicitly excluded by the handoff.
- Single process doing work inside HTTP requests: violates "no long-running LLM work inside an HTTP request".

## Consequences
- Module boundaries (api → services → repositories; orchestration uses services) must be enforced by convention and review.
- Workers can scale independently of the API later without a code split.
