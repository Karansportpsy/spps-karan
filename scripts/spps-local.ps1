param(
    [ValidateSet("Status", "Start")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "`nSPPS Local Development" -ForegroundColor Cyan
Write-Host "Workspace: $root"

# Verify the existing PostgreSQL database.
$health = docker inspect --format "{{.State.Health.Status}}" `
    spps-canonical-database-1

if ($LASTEXITCODE -ne 0 -or $health -ne "healthy") {
    throw "STOP: PostgreSQL is not healthy. Check Docker Desktop."
}

Write-Host "PostgreSQL: HEALTHY" -ForegroundColor Green

# Prevent duplicate FastAPI servers.
$listener = Get-NetTCPConnection `
    -LocalPort 8000 `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($listener) {
    $processInfo = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $($listener.OwningProcess)"

    if (-not $processInfo -or
        $processInfo.CommandLine -notmatch 'tools\.development_server') {
        throw "STOP: Port 8000 belongs to another process."
    }

    $response = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8000/health/ready" `
        -UseBasicParsing `
        -TimeoutSec 10

    if ($response.StatusCode -ne 200) {
        throw "STOP: SPPS backend readiness check failed."
    }

    Write-Host "FastAPI: RUNNING (PID $($listener.OwningProcess))" `
        -ForegroundColor Green

    Write-Host "Application: http://127.0.0.1:8000/studio"
    return
}

Write-Host "FastAPI: NOT RUNNING" -ForegroundColor Yellow

if ($Action -eq "Start") {
    if (-not (Test-Path ".\.env.integration")) {
        throw "STOP: Integration configuration missing."
    }

    Write-Host "Starting the existing SPPS development server..."

    & ".\.venv\Scripts\python.exe" -m tools.development_server

    if ($LASTEXITCODE -ne 0) {
        throw "SPPS development server exited with an error."
    }
}
