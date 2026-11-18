$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Push-Location $root

try {
    Write-Host "`nSPPS Development Validation" -ForegroundColor Cyan

    if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
        throw "STOP: Python virtual environment missing."
    }

    if (-not (Test-Path ".\node_modules")) {
        throw "STOP: Node.js dependencies missing."
    }

    Write-Host "`n[1/2] Verifying consolidated source..." `
        -ForegroundColor Yellow

    & ".\.venv\Scripts\python.exe" -m tools.verify_full_stack

    if ($LASTEXITCODE -ne 0) {
        throw "FAIL: Source integrity verification."
    }

    Write-Host "`n[2/2] Running Android source tests..." `
        -ForegroundColor Yellow

    npm run test:android

    if ($LASTEXITCODE -ne 0) {
        throw "FAIL: Android source tests."
    }

    Write-Host "`nALL SELECTED SPPS VALIDATION CHECKS PASSED" `
        -ForegroundColor Green
}
finally {
    Pop-Location
}
