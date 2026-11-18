param(
    [ValidateSet("Check", "Build")]
    [string]$Action = "Check"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location $root

try {
    Write-Host "`nSPPS Android Development" -ForegroundColor Cyan

    $expectedBranch = "integration/spps-full-stack-consolidation-2026-11-10"

    if ((git branch --show-current) -ne $expectedBranch) {
        throw "STOP: Incorrect Git branch."
    }

    git diff --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "STOP: Tracked source files contain changes."
    }

    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "STOP: Staged changes detected."
    }

    foreach ($file in @(
        ".\package.json",
        ".\android\gradlew.bat",
        ".\android\app\build.gradle",
        ".\node_modules",
        ".\.venv\Scripts\python.exe"
    )) {
        if (-not (Test-Path $file)) {
            throw "STOP: Missing prerequisite: $file"
        }
    }

    $sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"

    if (-not (Test-Path "$sdk\platforms\android-36")) {
        throw "STOP: Android SDK Platform 36 missing."
    }

    $env:ANDROID_HOME = $sdk
    $env:ANDROID_SDK_ROOT = $sdk

    Write-Host "Git branch: VERIFIED" -ForegroundColor Green
    Write-Host "Tracked source: CLEAN" -ForegroundColor Green
    Write-Host "Android SDK: FOUND" -ForegroundColor Green

    $apk = ".\android\app\build\outputs\apk\debug\app-debug.apk"

    if (Test-Path $apk) {
        $item = Get-Item $apk
        Write-Host "Existing APK: $($item.Length) bytes" `
            -ForegroundColor Green
    } else {
        Write-Host "Existing APK: NOT FOUND" `
            -ForegroundColor Yellow
    }

    if ($Action -eq "Check") {
        Write-Host "`nANDROID ENVIRONMENT CHECK COMPLETE" `
            -ForegroundColor Green
        return
    }

    # Explicit Build mode only.
    $env:SPPS_ANDROID_DEV_URL = "http://localhost:8000/studio"

    try {
        Write-Host "`nChecking npm dependencies..." `
            -ForegroundColor Yellow

        npm audit --audit-level=high

        if ($LASTEXITCODE -ne 0) {
            throw "STOP: npm security audit failed."
        }

        Write-Host "`nConfiguring Android debug mode..." `
            -ForegroundColor Yellow

        npm run android:configure:debug
        if ($LASTEXITCODE -ne 0) {
            throw "Android configuration failed."
        }

        npm run android:sync
        if ($LASTEXITCODE -ne 0) {
            throw "Capacitor synchronization failed."
        }

        npm run test:android
        if ($LASTEXITCODE -ne 0) {
            throw "Android source tests failed."
        }

        Write-Host "`nBuilding debug APK..." `
            -ForegroundColor Yellow

        & ".\android\gradlew.bat" -p android assembleDebug --console=plain

        if ($LASTEXITCODE -ne 0) {
            throw "Android Gradle build failed."
        }

        if (-not (Test-Path $apk)) {
            throw "APK output missing."
        }

        Write-Host "`nAPK BUILD SUCCESSFUL" -ForegroundColor Green

        Get-FileHash $apk -Algorithm SHA256 |
            Format-List Algorithm, Hash
    }
    finally {
        Write-Host "`nRestoring bundled source configuration..."

        npm run android:configure:bundle
        if ($LASTEXITCODE -ne 0) {
            throw "Bundle configuration restoration failed."
        }

        npm run android:sync
        if ($LASTEXITCODE -ne 0) {
            throw "Bundled asset synchronization failed."
        }

        Remove-Item Env:\SPPS_ANDROID_DEV_URL `
            -ErrorAction SilentlyContinue

        Write-Host "`nReview Git status after build:"
        git status --short
    }
}
finally {
    Pop-Location
}
