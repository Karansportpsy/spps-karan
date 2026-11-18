# Compares the exact web/app.js source with the bytes served by FastAPI.
# Safe diagnostics: no process termination, no invitation tokens printed.
param([string]$ProjectRoot=(Split-Path -Parent $PSScriptRoot),[int]$Port=8000)
$ErrorActionPreference='Stop'
$root=(Resolve-Path $ProjectRoot).Path
$source=Join-Path $root 'web\app.js'
if (-not (Test-Path $source)) { throw "Cannot find $source" }
$owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($owner) {
  Write-Host "Listening on port $($Port): PID $($owner.OwningProcess)"
  $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.OwningProcess)"
  if ($process) { Write-Host "Command line: $($process.CommandLine)" }
} else { throw "No backend listening on port $Port. Start the correct SPPS backend." }
$version=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$url="http://127.0.0.1:$Port/assets/app.js?diagnostic=$version"
$response=Invoke-WebRequest -Uri $url -UseBasicParsing -Headers @{'Cache-Control'='no-cache'}
$sha=[System.Security.Cryptography.SHA256]::Create()
try {
  $bytesSource=[System.IO.File]::ReadAllBytes($source)
  $bytesLive=[System.Text.Encoding]::UTF8.GetBytes([string]$response.Content)
  $localHash=[BitConverter]::ToString($sha.ComputeHash($bytesSource)).Replace("-", "").ToLowerInvariant()
  $liveHash=[BitConverter]::ToString($sha.ComputeHash($bytesLive)).Replace("-", "").ToLowerInvariant()
} finally { $sha.Dispose() }
Write-Host "Source SHA256: $localHash"
Write-Host "Served SHA256: $liveHash"
if ($localHash -eq $liveHash) {
  Write-Host 'PASS — Backend serves this exact project web/app.js.' -ForegroundColor Green
} else {
  Write-Warning 'MISMATCH — Backend is serving an older/different app.js. Check the process working directory. Do not rebuild the APK yet.'
  exit 1
}
