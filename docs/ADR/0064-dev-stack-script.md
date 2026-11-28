# ADR-0064: One command for the local stack

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.18

## Context
- **Running AgentFlow locally means five things.** PostgreSQL and Redis, the API, the Celery worker, Celery Beat and the web app, plus idempotent setup: migrations, roles, built-in agents and prices. People started them by hand in four terminals.
- **Problems we hit:**
  - **Docker stopped overnight** and everything failed with connection timeouts, without a clear signal.
  - **Port clash:** AgentFlow's API used port 8000, uvicorn's default, which every generated FastAPI project also uses.
  - **Two workers on different code:** a worker on older code (started separately) shared the broker with a new one. The two split the jobs, and an integration run went to the old worker, which skipped the new browser flows. A project was reported verified without its flows (progress.md, Step 9.16).

## Decision
`scripts/dev.ps1 <up|down|restart|status|logs> [-ApiPort 8010] [-WebPort 3000]`:
- **`up`:**
  1. Starts Docker Desktop if needed and waits for it.
  2. Runs `infra.ps1 up`.
  3. Runs `alembic upgrade head`, `app.db.roles provision`, `app.agents.seed` and `app.llm.pricing load` (all idempotent).
  4. Starts the API (port **8010** by default), the worker, Beat and `next dev` (with `NEXT_PUBLIC_API_BASE_URL` set to the API) in the background.
  5. Waits for `/health/ready` and prints the status.

  Process ids and logs go to `.agentflow-dev/` (gitignored).
- **`up` refuses** while the chosen ports are in use, or while **any** AgentFlow process of this repository runs, whether or not it was started by the script. The check matches the API, worker, Beat and web command lines, and the error lists them.
- **`down`** stops the tracked processes and every other AgentFlow process of this repository, then PostgreSQL and Redis. It never removes volumes.
- **`status`** shows Docker, each process, other AgentFlow processes, API readiness and the sandbox images. **`logs`** shows the last lines of each log.

## Consequences
- **One command to start and stop,** and generated apps can keep port 8000.
- **Mixed-version workers are prevented** for stacks started with the script. A worker started by hand next to it is still possible. Workers could later check the code version in task messages.
- **Windows and PowerShell only,** like `infra.ps1` and `sandbox.ps1`.
