@echo off
setlocal
cd /d "%~dp0"
echo ===== SPPS Version 1.0 - Free - Android developer setup =====
where node >nul 2>&1 || (echo ERROR: Install Node.js 22 or newer & exit /b 1)
where npm >nul 2>&1 || (echo ERROR: npm is missing & exit /b 1)
node --version
echo Installing the PINNED Capacitor 8.6.0 set and creating the matching lockfile.
call npm install --save-exact
if errorlevel 1 (echo ERROR: npm install failed. Check internet and proxy. & exit /b 1)
call npm audit --audit-level=high
if errorlevel 1 (echo ERROR: npm audit found security advisories. Setup blocked. & exit /b 1)
call npm ls @capacitor/android @capacitor/core @capacitor/cli tar
if errorlevel 1 (echo ERROR: Capacitor dependency validation failed. & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Configure-AndroidSDK.ps1"
if errorlevel 1 (echo ERROR: Android SDK configuration failed. & exit /b 1)
call npm run android:configure:debug
if errorlevel 1 exit /b 1
call npm run android:init
if errorlevel 1 exit /b 1
call npm run test:android
if errorlevel 1 exit /b 1
echo.
echo Ready: open folder android in Android Studio.
echo Then run run_android_debug_windows.bat with the backend running and Android connected.
endlocal
