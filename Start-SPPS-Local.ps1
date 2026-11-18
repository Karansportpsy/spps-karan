# SPPS Version 1.0 - Free: run the web app on this computer against your Supabase database.
#
# 1. Copy .env.production.example to .env.local and fill in the SAME values you set in Render
#    (database URLs with passwords, Supabase keys, SPPS_FORM_TOKEN_SECRET, SPPS_FIELD_KEYS, ...).
#    .env.local holds secrets: never share it or put it in a zip or repository.
# 2. In Supabase -> Authentication -> URL Configuration, add http://localhost:8000/register and
#    http://localhost:8000/studio to the redirect URLs.
# 3. Run (PowerShell, from this folder):  powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-SPPS-Local.ps1
# 4. Open http://localhost:8000 in Chrome or Edge. Press Ctrl+C in this window to stop.
#
# This computer and Render use the same database, so anything you do here is real data.
param([string]$EnvFile = ".env.local", [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path $EnvFile)) { throw "Create $EnvFile first: copy .env.production.example and fill in your Render values." }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is already in use. Stop the other program or choose -Port 8001." }

# Load KEY=VALUE lines (comments and blank lines ignored; surrounding quotes removed; trailing comments removed).
$loaded = 0
foreach ($line in Get-Content $EnvFile) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $key, $value = $t.Split('=', 2)
    $key = $key.Trim(); $value = $value.Trim()
    if ($value.StartsWith('"') -and $value.EndsWith('"') -and $value.Length -ge 2) { $value = $value.Substring(1, $value.Length - 2) }
    elseif ($value -match '^(.*?)\s+#') { $value = $Matches[1] }
    if ($value -match '<[^>]+>') { throw "$key still contains a placeholder ($value). Put your real value in $EnvFile." }
    Set-Item -Path "Env:$key" -Value $value
    $loaded++
}
foreach ($need in 'DATABASE_URL','SPPS_AUTH_DATABASE_URL','SPPS_SUPABASE_URL','SPPS_SUPABASE_PUBLISHABLE_KEY','SPPS_FORM_TOKEN_SECRET','SPPS_FIELD_KEYS','SPPS_FIELD_KEY_CURRENT') {
    if (-not (Get-Item -Path "Env:$need" -ErrorAction SilentlyContinue)) { throw "$need is missing from $EnvFile" }
}
# Local differences only: accept this computer's address and read the client address directly (no proxy here).
$hosts = @(($env:MODEL_C_ALLOWED_HOSTS -split ',') + 'localhost' + '127.0.0.1' | Where-Object { $_ } | Select-Object -Unique)
$env:MODEL_C_ALLOWED_HOSTS = ($hosts -join ',')
$env:SPPS_TRUSTED_PROXY_HOPS = '0'
Write-Host "Loaded $loaded settings from $EnvFile (values are not shown)."

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Creating the Python environment (first run only)...'
    py -3 -m venv .venv 2>$null; if (-not (Test-Path '.venv\Scripts\python.exe')) { python -m venv .venv }
    if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Install Python 3.12 or newer from python.org, then run this again.' }
}
& .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements-hosted.txt
if ($LASTEXITCODE -ne 0) { throw 'Installing the Python packages failed.' }

Write-Host ''
Write-Host '======================================================='
Write-Host ' SPPS Version 1.0 - Free  (running on this computer)'
Write-Host " Open http://localhost:$Port"
Write-Host " Practitioner sign-in  http://localhost:$Port/studio"
Write-Host " Athlete app           http://localhost:$Port/athlete"
Write-Host " Health check          http://localhost:$Port/health/ready"
Write-Host ' Press Ctrl+C to stop.'
Write-Host '======================================================='
Start-Process "http://localhost:$Port"
& .venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port $Port --no-access-log
