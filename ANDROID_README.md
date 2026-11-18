# SPPS Version 1.0 - Free — Android

Package: `com.winmindperform.spps.beta15` (the debug build adds `.m12validation`). The package id is unchanged so a
new APK installs over an earlier SPPS debug build on the same phone. App name on the phone: **SPPS Free 1.0**.
Version: 1.0.0-free (version code 151).

The app opens your hosted SPPS address over HTTPS, so it always shows the same accounts, records and links as the
website. E-mailed links work in Chrome or in the app.

## Build the APK (Windows)

You need: Node.js 22 or newer, Android Studio (Android SDK Platform 36), and the hosted https address of your SPPS.

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-SPPS-1.0-Free-APK.ps1 -HostedOrigin https://app.winmindperform.com
```

The script installs the pinned packages, runs the dependency security check and the Android source tests, sets the
hosted address with unencrypted traffic switched off, builds the APK and prints its SHA-256. The file is
`output\SPPS-1.0-Free.apk`. Add `-Install` to install it on a phone connected with USB debugging.

This is a debug-signed APK for direct installation. A Google Play release needs your own release keystore and Play
Console listing, which are not part of this folder.

`setup_android_windows.bat` and `run_android_debug_windows.bat` are developer tools that point the app at a backend
running on this computer through USB forwarding; you do not need them to build the hosted APK.
