// Starts the real backend for E2E tests: a disposable database, the API, a Celery worker and
// Celery Beat. Nothing else is mocked: the built-in agent runtimes execute tasks with the mock
// model provider (forced below, whatever .env says), so no paid AI API is ever called.

import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { createWriteStream, mkdirSync, rmSync } from "node:fs";
import path from "node:path";

export const API_PORT = 8011;
export const WEB_PORT = 3100;
export const API_URL = `http://localhost:${API_PORT}`;
export const WEB_URL = `http://localhost:${WEB_PORT}`;

const FRONTEND_DIR = path.resolve(__dirname, "..", "..");
const BACKEND_DIR = path.resolve(FRONTEND_DIR, "..", "backend");
const RUN_DIR = path.join(FRONTEND_DIR, "test-results", "e2e-backend");

/** Overrides layered on the repo-root .env; secrets still come from .env, never from here. */
export function backendEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    POSTGRES_DB: "agentflow_e2e",
    REDIS_DB: "2",
    CELERY_BROKER_REDIS_DB: "3",
    CORS_ALLOWED_ORIGINS: JSON.stringify([WEB_URL]),
    // Every test signs in from 127.0.0.1; the default limits are tuned for real users.
    AUTH_LOGIN_ATTEMPTS_PER_MINUTE: "1000",
    AUTH_REQUESTS_PER_MINUTE: "10000",
    SERVICE_NAME: "agentflow-e2e",
    // Deterministic, free model replies even when .env configures a real provider (ADR-0020).
    MODEL_PROVIDER: "mock",
    MODEL_NAME: "mock-1",
    PYTHONUNBUFFERED: "1",
  };
}

function run(label: string, args: string[]): void {
  // PYTHONPATH lets a script outside backend/ import the `app` package.
  const env = { ...backendEnv(), PYTHONPATH: BACKEND_DIR };
  const result = spawnSync("uv", ["run", ...args], { cwd: BACKEND_DIR, env, encoding: "utf-8" });
  if (result.status !== 0) {
    throw new Error(`${label} failed (exit ${result.status}):\n${result.stdout}\n${result.stderr}`);
  }
}

export function prepareDatabase(): void {
  run("reset e2e database", ["python", path.join(FRONTEND_DIR, "e2e", "support", "reset_db.py")]);
  run("migrate", ["alembic", "upgrade", "head"]);
  run("seed built-in agents", ["python", "-m", "app.agents.seed"]);
  // The API runs as the restricted role under row-level security (ADR-0012), like production.
  run("provision API and worker roles", ["python", "-m", "app.db.roles", "provision"]);
}

function start(name: string, args: string[]): ChildProcess {
  const log = createWriteStream(path.join(RUN_DIR, `${name}.log`));
  const child = spawn("uv", ["run", ...args], { cwd: BACKEND_DIR, env: backendEnv(), windowsHide: true });
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  return child;
}

/** Stops a process and everything it started (uv -> python -> celery children). */
export function stopTree(child: ChildProcess): void {
  if (child.pid === undefined || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

async function waitForApi(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${API_URL}/api/v1/health/ready`);
      if (response.ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`API did not become ready on ${API_URL}; see ${RUN_DIR}`);
}

export async function startBackend(): Promise<ChildProcess[]> {
  rmSync(RUN_DIR, { recursive: true, force: true });
  mkdirSync(RUN_DIR, { recursive: true });
  prepareDatabase();
  const processes = [
    start("api", [
      "uvicorn",
      "app.main:create_app",
      "--factory",
      "--port",
      String(API_PORT),
      "--loop",
      "asyncio:SelectorEventLoop",
    ]),
    start("worker", [
      "celery",
      "-A",
      "app.worker.celery_app",
      "worker",
      "--pool=solo",
      "-Q",
      "execution,maintenance",
      "--loglevel=INFO",
    ]),
    start("beat", [
      "celery",
      "-A",
      "app.worker.celery_app",
      "beat",
      `--schedule=${path.join(RUN_DIR, "celerybeat-schedule")}`,
      "--loglevel=INFO",
    ]),
  ];
  try {
    await waitForApi(60_000);
  } catch (error) {
    processes.forEach(stopTree);
    throw error;
  }
  return processes;
}
