# ONLY for the separately extracted SPPS Beta 1.4 source. Do not run on 1.3.1.
# Installs a stable Capacitor 8 patch release, then reports npm advisories.
# Package files are backed up before any change. No --force upgrades.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path '.\tests_android\v14_delivery_contract.test.mjs')) {
  throw 'This is not the SPPS Beta 1.4 working copy. Stop.'
}
$backup = Join-Path $root 'security-review-backup'
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item '.\package.json' (Join-Path $backup 'package.before-security-upgrade.json') -Force
Copy-Item '.\package-lock.json' (Join-Path $backup 'package-lock.before-security-upgrade.json') -Force
Write-Host 'Upgrading Capacitor packages as a version-matched set (8.6.0, stable on 2026-11-05).'
& npm install --save-exact '@capacitor/core@8.6.0' '@capacitor/android@8.6.0'
if ($LASTEXITCODE -ne 0) { throw 'Capacitor runtime upgrade failed. Original manifests saved.' }
& npm install --save-dev --save-exact '@capacitor/cli@8.6.0'
if ($LASTEXITCODE -ne 0) { throw 'Capacitor CLI upgrade failed. Original manifests saved.' }
& npm run test:android
if ($LASTEXITCODE -ne 0) { throw 'Android contract tests failed after package upgrade.' }
& npm audit --audit-level=high
if ($LASTEXITCODE -ne 0) {
  Write-Warning 'npm audit still reports high/critical issues; DO NOT publish or mark security gate passed.'
  exit 2
}
Write-Host 'npm audit high/critical gate passed. Rebuild and test APK and WebRTC on device before promotion.'
