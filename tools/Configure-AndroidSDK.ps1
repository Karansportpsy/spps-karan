# SPPS Version 1.0 - Free: discover the local Android SDK for Gradle without editing other projects.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$propertiesFile = Join-Path $root "android\local.properties"
if (Test-Path $propertiesFile) {
    $line = Get-Content $propertiesFile | Where-Object { $_ -match '^sdk\.dir=' } | Select-Object -First 1
    if (-not $line) { Write-Error "Existing android/local.properties has no sdk.dir. Correct it manually; file was not changed."; exit 1 }
    $current = ($line -replace '^sdk\.dir=', '').Replace('/', '\')
    if (-not (Test-Path (Join-Path $current 'platforms\android-36'))) {
        Write-Error "Existing SDK path in android/local.properties does not contain Platform 36: $current"
        exit 1
    }
    Write-Host "Using existing Android SDK configuration: $current"
    exit 0
}
$candidates = @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA 'Android\Sdk')) | Where-Object { $_ }
$sdk = $null
foreach ($candidate in $candidates) {
    if (Test-Path (Join-Path $candidate 'platforms\android-36')) { $sdk = $candidate; break }
}
if (-not $sdk) {
    Write-Error "Android SDK Platform 36 not found. Install it through Android Studio > SDK Manager, or set ANDROID_HOME."
    exit 1
}
$sdkPath = $sdk.Replace('\','/')
Set-Content -LiteralPath $propertiesFile -Encoding ASCII -Value "sdk.dir=$sdkPath"
Write-Host "Created Android SDK configuration for this project: $sdkPath"
