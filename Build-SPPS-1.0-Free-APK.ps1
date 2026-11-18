# SPPS Version 1.0 - Free: build the Android APK that opens your hosted SPPS address.
# Usage (PowerShell, from this folder):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-SPPS-1.0-Free-APK.ps1 -HostedOrigin https://app.winmindperform.com
# Add -Install to install it on a phone connected with USB debugging (adb).
# The package id is unchanged (com.winmindperform.spps.beta15, debug build suffix .m12validation), so this APK
# replaces an earlier SPPS debug build on the same phone. It is a signed DEBUG APK for direct installation;
# a Google Play release needs your own release keystore and is not built here.
param([Parameter(Mandatory=$true)][string]$HostedOrigin, [switch]$Install)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$pkg=Get-Content '.\package.json' -Raw | ConvertFrom-Json
if($pkg.version -ne '1.0.0-free' -or $pkg.dependencies.'@capacitor/core' -ne '8.6.0') { throw 'This folder is not the SPPS Version 1.0 - Free source' }
try { $parsed=[uri]$HostedOrigin } catch { throw 'Give the full https address of your hosted SPPS, for example https://app.winmindperform.com' }
if ($parsed.Scheme -ne 'https' -or $parsed.AbsolutePath -ne '/' -or $parsed.Query -or $parsed.Fragment -or $parsed.UserInfo -or $parsed.Host -eq 'localhost') { throw 'The hosted address must be an https address with no path, query or user name' }
$env:SPPS_ANDROID_DEV_URL=$HostedOrigin.TrimEnd('/')
function Checked([string]$Title,[scriptblock]$Command){ Write-Host "---- $Title ----"; & $Command; if($LASTEXITCODE -ne 0){throw "$Title failed; no APK was produced"} }
if(-not(Test-Path 'package-lock.json')){Checked 'Create pinned lockfile' { npm install --package-lock-only --ignore-scripts }}
Checked 'Install pinned packages' { npm ci }
Checked 'Dependency security check' { npm audit --audit-level=high }
Checked 'Android source tests' { npm run test:android }
Checked 'Set the hosted HTTPS address' { npm run android:configure:hosted-beta }
Checked 'Prepare the Android project' { npm run android:init }
Checked 'Copy the web app into Android' { npm run android:sync }
$native=Get-Content '.\android\app\src\debug\AndroidManifest.xml' -Raw
if($native -notmatch 'usesCleartextTraffic="false"'){throw 'The APK would allow unencrypted traffic; stopping'}
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools\Configure-AndroidSDK.ps1')
if($LASTEXITCODE -ne 0){throw 'Android SDK configuration failed'}
Push-Location android
try { Checked 'Build the APK' { & '.\gradlew.bat' assembleDebug } } finally { Pop-Location }
$apk=Join-Path $root 'android\app\build\outputs\apk\debug\app-debug.apk'
if(-not(Test-Path $apk)){throw 'APK not found after the build'}
$dest=Join-Path $root 'output\SPPS-1.0-Free.apk'
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
Copy-Item -LiteralPath $apk -Destination $dest -Force
Get-FileHash $dest -Algorithm SHA256 | Format-List
Write-Host "SPPS Version 1.0 - Free APK: $dest"
if($Install){ Checked 'Install on the connected phone' { adb install -r $dest } }
