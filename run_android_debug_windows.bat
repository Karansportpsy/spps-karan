@echo off
setlocal
cd /d "%~dp0"
echo ===== SPPS Android - compile/install debug APK =====
if not exist "package-lock.json" (echo ERROR: Run setup_android_windows.bat and the security audit before building. & exit /b 1)
call npm audit --audit-level=high
if errorlevel 1 (echo ERROR: Security audit failed. Debug APK build blocked. & exit /b 1)
call npm run test:android
if errorlevel 1 (echo ERROR: Android contract test failed. Debug APK build blocked. & exit /b 1)
if not exist "android\gradlew.bat" (echo ERROR: First run setup_android_windows.bat & exit /b 1)
where adb >nul 2>&1 || (echo ERROR: adb missing. Add Android SDK platform-tools to PATH. & exit /b 1)
call adb devices
if not defined SPPS_BACKEND_PORT set "SPPS_BACKEND_PORT=8000"
echo Android localhost:8000 forwarding to Windows localhost:%SPPS_BACKEND_PORT%
call adb reverse tcp:8000 tcp:%SPPS_BACKEND_PORT%
if errorlevel 1 (echo ERROR: Check backend port, connect Android USB with debugging enabled, and accept RSA prompt. & exit /b 1)
call npm run android:configure:debug
if errorlevel 1 exit /b 1
call npm run android:sync
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Configure-AndroidSDK.ps1"
if errorlevel 1 (echo ERROR: Android SDK configuration failed. & exit /b 1)
pushd android
call gradlew.bat assembleDebug
if errorlevel 1 (popd & echo ERROR: Gradle build failed. Review Android SDK installation. & exit /b 1)
popd
if not exist output mkdir output
copy /Y "android\app\build\outputs\apk\debug\app-debug.apk" "output\SPPS-1.0-Free-local-debug.apk" >nul
if errorlevel 1 (echo ERROR: APK not found after build. & exit /b 1)
echo APK: output\SPPS-1.0-Free-local-debug.apk
call adb install -r "output\SPPS-1.0-Free-local-debug.apk"
if errorlevel 1 (echo APK generated, but installation failed. & exit /b 1)
echo Installed on connected device. Keep FastAPI server running on Windows port %SPPS_BACKEND_PORT%.
endlocal
