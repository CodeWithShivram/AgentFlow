# Manage local PostgreSQL and Redis (Docker Compose).
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 <up|down|ps|logs|restart|destroy-data>
# 'down' and 'restart' keep data volumes. Only 'destroy-data' deletes them, and it asks for confirmation.
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('up', 'down', 'ps', 'logs', 'restart', 'destroy-data')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
$composeFile = Join-Path $root 'infrastructure/docker/docker-compose.yml'

if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy .env.example to .env and set the passwords."
}
$compose = @('compose', '--env-file', $envFile, '-f', $composeFile)

function Invoke-Compose([string[]]$Arguments) {
    & docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

switch ($Action) {
    'up' { Invoke-Compose @('up', '-d', '--wait') }
    'down' { Invoke-Compose @('down') }
    'ps' { Invoke-Compose @('ps') }
    'logs' { Invoke-Compose @('logs', '--tail', '200') }
    'restart' { Invoke-Compose @('down'); Invoke-Compose @('up', '-d', '--wait') }
    'destroy-data' {
        $answer = Read-Host 'This permanently deletes local PostgreSQL and Redis data. Type DELETE to continue'
        if ($answer -ne 'DELETE') { Write-Host 'Aborted.'; return }
        Invoke-Compose @('down', '--volumes')
    }
}
