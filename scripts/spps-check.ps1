<#
.SYNOPSIS
  SPPS automated test runner (wrapper for tools/spps_check.py).
.EXAMPLE
  .\scripts\spps-check.ps1                    # fast: static, unit, android-src
  .\scripts\spps-check.ps1 -Tier db           # disposable PostgreSQL: identity, integration, browser
  .\scripts\spps-check.ps1 -Tier full         # everything except the APK build
  .\scripts\spps-check.ps1 -Tier android      # debug APK build + package identity
  .\scripts\spps-check.ps1 -RerunFailed       # replay only failures from the last report
  .\scripts\spps-check.ps1 -Tier db -Repeat 3 # flaky-test detection
Exit: 0 pass, 1 test failure, 2 safety refusal, 3 protected env changed, 4 tracked files modified
#>
param(
    [string]$Tier = "fast",
    [switch]$RerunFailed,
    [int]$Repeat = 1,
    [switch]$KeepDb,
    [switch]$UpdateBaseline
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { throw "STOP: .venv missing" }
    $argsList = @("-m", "tools.spps_check", "--tier", $Tier, "--repeat", $Repeat)
    if ($RerunFailed) { $argsList += "--rerun-failed" }
    if ($KeepDb) { $argsList += "--keep-db" }
    if ($UpdateBaseline) { $argsList += "--update-baseline" }
    & $python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
