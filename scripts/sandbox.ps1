# Builds the sandbox images for the stack profiles (ADR-0050). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts/sandbox.ps1 build
# Images are built locally and never pulled at run time; the sandbox refuses missing images.
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('build', 'list')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
# Keep in sync with app/projects/profiles.py (StackProfile.image).
$images = @{
    'python-3.12'        = 'agentflow-sandbox/python-3.12:1'
    'node-24-typescript' = 'agentflow-sandbox/node-24-typescript:1'
}
switch ($Action) {
    'build' {
        foreach ($profile in $images.Keys) {
            $context = Join-Path $root "infrastructure/sandbox/$profile"
            & docker build --pull=false --tag $images[$profile] $context
            if ($LASTEXITCODE -ne 0) { throw "building $profile failed (exit $LASTEXITCODE)" }
        }
    }
    'list' { & docker image ls 'agentflow-sandbox/*' }
}
