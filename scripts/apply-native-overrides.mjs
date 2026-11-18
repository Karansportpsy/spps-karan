/** Repeatable manifest patch: native media access plus debug-only cleartext for adb reverse. */
import {existsSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const main=join(root,'android/app/src/main/AndroidManifest.xml');
if(!existsSync(main))throw Error('Native Android project not found. Run npm run android:init first.');
let xml=readFileSync(main,'utf8');
for(const permission of ['android.permission.INTERNET','android.permission.CAMERA','android.permission.RECORD_AUDIO','android.permission.MODIFY_AUDIO_SETTINGS']){
 if(!xml.includes(`android:name="${permission}"`))xml=xml.replace(/<application\b/,`<uses-permission android:name="${permission}" />\n    <application`);
}
for(const feature of ['android.hardware.camera','android.hardware.microphone']){
 if(!xml.includes(`android:name="${feature}"`))xml=xml.replace(/<application\b/,`<uses-feature android:name="${feature}" android:required="false" />\n    <application`);
}
const appTag=xml.match(/<application\b[^>]*>/);
if(!appTag)throw Error('AndroidManifest.xml application tag not found');
if(!appTag[0].includes('android:usesCleartextTraffic')){
 xml=xml.replace(/<application\b/,`<application android:usesCleartextTraffic="false"`);
}else{
 xml=xml.replace(/(<application\b[^>]*android:usesCleartextTraffic=")[^"]+("[^>]*>)/,'$1false$2');
}
// Protect sensitive application state against normal Android backup/restore.
const appWithBackup=xml.match(/<application\b[^>]*>/);
if(appWithBackup){
 if(/android:allowBackup="[^"]*"/.test(appWithBackup[0])){
  xml=xml.replace(/(<application\b[^>]*android:allowBackup=")[^"]+("[^>]*>)/,'$1false$2');
 } else {
  xml=xml.replace(/<application\b/,`<application android:allowBackup="false"`);
 }
}
const activity=xml.match(/<activity\b[^>]*>/);
if(activity && !activity[0].includes('android:windowSoftInputMode')){
 xml=xml.replace(/<activity\b/,`<activity android:windowSoftInputMode="adjustResize"`);
}
writeFileSync(main,xml);
// A hosted APK must not acquire the localhost debug cleartext exception.
const cfg=JSON.parse(readFileSync(join(root,'capacitor.config.json'),'utf8'));
const hostedMode=typeof cfg?.server?.url==='string' && cfg.server.url.startsWith('https://');
// Debug and RC-validation builds share the localhost-only cleartext exception (adb reverse).
for(const variant of ['debug','rcvalidation']){
const debug=join(root,`android/app/src/${variant}/AndroidManifest.xml`);
mkdirSync(dirname(debug),{recursive:true});
writeFileSync(debug,`<?xml version="1.0" encoding="utf-8"?>\n<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools">\n  <!-- Cleartext allowed only for local ADB reverse, not hosted HTTPS beta. -->\n  <application android:usesCleartextTraffic="${hostedMode?'false':'true'}" tools:replace="android:usesCleartextTraffic" />\n</manifest>\n`);
}
const strings=join(root,'android/app/src/main/res/values/strings.xml');
if(existsSync(strings)){
 let s=readFileSync(strings,'utf8');
 s=s.replace(/(<string name="app_name">)[^<]*(<\/string>)/,'$1SPPS Free 1.0$2');
 s=s.replace(/(<string name="title_activity_main">)[^<]*(<\/string>)/,'$1SPPS Free 1.0$2');
 writeFileSync(strings,s);
}
console.log('Android native manifest patched. HTTPS-hosted cleartext:',hostedMode?'disabled':'local-only debug exception');
