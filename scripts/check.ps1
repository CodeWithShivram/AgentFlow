# Runs every configured quality gate for backend and frontend.
# Usage (from repo root): powershell -ExecutionPolicy Bypass -File scripts/check.ps1 [-E2E]
# -E2E also runs the Playwright journeys against a real backend (needs infra.ps1 up; ~6 min).
param([switch]$E2E)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
}

Push-Location (Join-Path $root 'backend')
try {
    Invoke-Step 'backend: ruff format --check' { uv run ruff format --check . }
    Invoke-Step 'backend: ruff check' { uv run ruff check . }
    Invoke-Step 'backend: mypy' { uv run mypy app tests alembic }
    Invoke-Step 'backend: alembic check (models match migrations)' { uv run alembic check }
    Invoke-Step 'backend: pytest' { uv run pytest }
}
finally { Pop-Location }

Push-Location (Join-Path $root 'frontend')
try {
    Invoke-Step 'frontend: lint' { npm run lint }
    Invoke-Step 'frontend: typecheck' { npm run typecheck }
    Invoke-Step 'frontend: test' { npm test }
    # E2E builds with the E2E API URL, so it runs before the final build.
    if ($E2E) { Invoke-Step 'frontend: e2e (Playwright)' { npm run test:e2e } }
    Invoke-Step 'frontend: build' { npm run build }
}
finally { Pop-Location }

Write-Host 'All checks passed.' -ForegroundColor Green
