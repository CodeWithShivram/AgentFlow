# Runs the whole AgentFlow stack locally with one command (Step 9.18, ADR-0064).
# Usage (from the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 <up|down|restart|status|logs> [-ApiPort 8010] [-WebPort 3000]
#
#   up       Docker check, PostgreSQL/Redis, migrations, roles, built-in agents and prices (all
#            idempotent), then the API, Celery worker, Celery Beat and the web app, in the background.
#   down     Stops the four processes, any other AgentFlow API, worker, Beat or web process of this
#            repository, and PostgreSQL/Redis. Data volumes are always kept.
#   restart  down, then up.
#   status   What runs, the API's readiness, Docker and the sandbox images.
#   logs     The last lines of each process log.
#
# The API defaults to port 8010, so generated projects can use 8000 (uvicorn's default) while
# AgentFlow runs. Process ids and logs live in .agentflow-dev/ (gitignored).
# 'up' refuses while any other AgentFlow process of this repository runs: two workers on different
# code versions share one broker and split the jobs between them (ADR-0064).
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('up', 'down', 'restart', 'status', 'logs')]
    [string]$Action,
    [int]$ApiPort = 8010,
    [int]$WebPort = 3000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$state = Join-Path $root '.agentflow-dev'
$pidFile = Join-Path $state 'pids.json'
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$images = @('agentflow-sandbox/python-3.12:1', 'agentflow-sandbox/node-24-typescript:1', 'agentflow-sandbox/python-3.12-web:1')

function Get-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $winget = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($winget) { return $winget.FullName }
    throw 'uv was not found. Install it (winget install astral-sh.uv) and try again.'
}

function Test-Docker {
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Wait-Docker {
    if (Test-Docker) { return }
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktop)) { throw 'Docker is not running and Docker Desktop was not found.' }
    Write-Host 'Starting Docker Desktop...'
    Start-Process $desktop | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Test-Docker) { return }
    }
    throw 'Docker Desktop did not start within 5 minutes.'
}

function Read-Pids {
    if (-not (Test-Path $pidFile)) { return @{} }
    $data = Get-Content $pidFile -Raw | ConvertFrom-Json
    $map = @{}
    foreach ($property in $data.PSObject.Properties) { $map[$property.Name] = [int]$property.Value }
    return $map
}

function Test-Alive([int]$ProcessId) {
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-AgentFlowProcesses {
    # The API, worker, Beat (uv and python launchers) and web dev server of this repository.
    $patterns = @('app.main:create_app', 'app.worker.celery_app')
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $line = $_.CommandLine
        if (-not $line) { return $false }
        $agentflow = $patterns | Where-Object { $line.Contains($_) }
        $web = $line.Contains('next') -and $line.Contains('dev') -and $line.Contains($frontend)
        ($agentflow -and ($_.Name -in 'uv.exe', 'python.exe', 'celery.exe', 'uvicorn.exe')) -or ($web -and $_.Name -eq 'node.exe')
    }
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$Directory) {
    Push-Location $Directory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$FilePath $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
    } finally { Pop-Location }
}

function Start-Background([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$Directory) {
    $out = Join-Path $state "$Name.log"
    $err = Join-Path $state "$Name.err.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $Directory `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    return $process.Id
}

function Stop-Stack {
    $pids = Read-Pids
    foreach ($name in $pids.Keys) {
        if (Test-Alive $pids[$name]) {
            & taskkill /PID $pids[$name] /T /F *> $null
            Write-Host "stopped $name (pid $($pids[$name]))"
        }
    }
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
    foreach ($process in @(Get-AgentFlowProcesses)) {
        if (Test-Alive $process.ProcessId) {
            & taskkill /PID $process.ProcessId /T /F *> $null
            Write-Host "stopped other AgentFlow process $($process.ProcessId) (started $($process.CreationDate))"
        }
    }
    if (Test-Docker) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'infra.ps1') down
    }
}

function Start-Stack {
    $running = Read-Pids
    if ($running.Count -gt 0 -and ($running.Values | Where-Object { Test-Alive $_ })) {
        throw 'AgentFlow is already running. Use "status", or "restart".'
    }
    $others = @(Get-AgentFlowProcesses)
    if ($others.Count -gt 0) {
        $list = ($others | ForEach-Object { "  pid $($_.ProcessId) started $($_.CreationDate)" }) -join "`n"
        throw "Other AgentFlow processes are running (an older worker would take jobs with old code):`n$list`nStop them with: scripts/dev.ps1 down"
    }
    foreach ($port in $ApiPort, $WebPort) {
        if (Test-Port $port) { throw "Port $port is in use. Stop what uses it or pass -ApiPort/-WebPort." }
    }
    New-Item -ItemType Directory -Force $state | Out-Null
    $uv = Get-Uv
    Wait-Docker
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'infra.ps1') up
    if ($LASTEXITCODE -ne 0) { throw 'Starting PostgreSQL and Redis failed.' }
    Write-Host 'Migrating and provisioning (idempotent)...'
    Invoke-Checked $uv @('run', 'alembic', 'upgrade', 'head') $backend
    Invoke-Checked $uv @('run', 'python', '-m', 'app.db.roles', 'provision') $backend
    Invoke-Checked $uv @('run', 'python', '-m', 'app.agents.seed') $backend
    Invoke-Checked $uv @('run', 'python', '-m', 'app.llm.pricing', 'load') $backend

    $pids = @{}
    $pids.api = Start-Background 'api' $uv @('run', 'uvicorn', 'app.main:create_app', '--factory', '--port', "$ApiPort", '--loop', 'asyncio:SelectorEventLoop') $backend
    $pids.worker = Start-Background 'worker' $uv @('run', 'celery', '-A', 'app.worker.celery_app', 'worker', '--pool=solo', '-Q', 'execution,maintenance', '--loglevel=INFO') $backend
    $pids.beat = Start-Background 'beat' $uv @('run', 'celery', '-A', 'app.worker.celery_app', 'beat', "--schedule=$(Join-Path $state 'celerybeat-schedule')", '--loglevel=INFO') $backend
    $env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:$ApiPort"
    $pids.web = Start-Background 'web' 'cmd.exe' @('/c', 'npm', 'run', 'dev', '--', '--port', "$WebPort") $frontend
    $pids | ConvertTo-Json | Set-Content -Encoding utf8 $pidFile

    $ready = $false
    for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest -UseBasicParsing "http://localhost:$ApiPort/api/v1/health/ready" -TimeoutSec 5 | Out-Null; $ready = $true } catch {}
    }
    if (-not $ready) { Write-Warning "The API is not ready yet. See: scripts/dev.ps1 logs" }
    Show-Status
    Write-Host ""
    Write-Host "AgentFlow: http://localhost:$WebPort   (API http://localhost:$ApiPort)"
}

function Show-Status {
    $pids = Read-Pids
    Write-Host "Docker:      $(if (Test-Docker) { 'running' } else { 'NOT RUNNING' })"
    foreach ($name in 'api', 'worker', 'beat', 'web') {
        $state_ = if ($pids.ContainsKey($name) -and (Test-Alive $pids[$name])) { "running (pid $($pids[$name]))" } else { 'stopped' }
        Write-Host ("{0,-12} {1}" -f "${name}:", $state_)
    }
    $tracked = @($pids.Values)
    $others = @(Get-AgentFlowProcesses | Where-Object { $tracked -notcontains $_.ProcessId -and $tracked -notcontains $_.ParentProcessId })
    if ($others.Count -gt 0) {
        Write-Host "Other:       $($others.Count) AgentFlow process(es) not started by this script (pids $((@($others | ForEach-Object { $_.ProcessId })) -join ', '))"
    }
    $readiness = 'not reachable'
    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://localhost:$ApiPort/api/v1/health/ready" -TimeoutSec 5
        $readiness = "ready (HTTP $($response.StatusCode))"
    } catch {
        if ($_.Exception.Response) { $readiness = "NOT READY (HTTP $([int]$_.Exception.Response.StatusCode))" }
    }
    Write-Host "API ready:   $readiness  (port $ApiPort)"
    if (Test-Docker) {
        $present = & docker image ls --format '{{.Repository}}:{{.Tag}}'
        $missing = $images | Where-Object { $present -notcontains $_ }
        $imageState = if ($missing) { "MISSING $($missing -join ', ') (run scripts/sandbox.ps1 build)" } else { 'all present' }
        Write-Host "Sandbox:     $imageState"
    }
}

switch ($Action) {
    'up' { Start-Stack }
    'down' { Stop-Stack }
    'restart' { Stop-Stack; Start-Stack }
    'status' { Show-Status }
    'logs' {
        foreach ($name in 'api', 'worker', 'beat', 'web') {
            foreach ($file in "$name.log", "$name.err.log") {
                $path = Join-Path $state $file
                if (Test-Path $path) {
                    Write-Host "===== $file ====="
                    Get-Content $path -Tail 15
                }
            }
        }
    }
}
