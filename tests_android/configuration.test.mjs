import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {buildConfig} from '../scripts/configure-android.mjs';
test('debug URL uses localhost and debug-only cleartext',()=>{
 const c=buildConfig('debug','http://localhost:8000/studio');
 assert.equal(c.server.url,'http://localhost:8000/studio');
 assert.equal(c.server.cleartext,true);
});
test('rejects insecure LAN address / credentials / unexpected mode',()=>{
 assert.throws(()=>buildConfig('debug','http://192.168.1.10:8000'));
 assert.throws(()=>buildConfig('debug','http://me:password@localhost:8000'));
 assert.throws(()=>buildConfig('release','https://example.com'));
});
test('accepts HTTPS server and never requests release cleartext',()=>{
 const c=buildConfig('debug','https://example.org');
 assert.equal(c.server.url,'https://example.org');
 assert.equal(c.server.cleartext,undefined);
});
test('bundled config does not embed development server',()=>{
 const c=buildConfig('bundle');
 assert.equal(c.server.url,undefined);
});
test('original SPPS front end and server remain present',()=>{
 for(const file of ['web/app.js','web/room.js','web/styles.css','server/main.py','server/subscription_routes.py']){
  assert(existsSync(resolve(file)));
 }
 const app=readFileSync('web/app.js','utf8');
 assert(app.includes('modelc-theme'));
 assert(app.includes('data-action="logout"') || app.includes("button('Logout','logout'"));
 assert(app.includes('state.freeAccess'));
 assert(app.includes('Android subscriptions are disabled in this beta') || app.includes('no subscription checkout')); 
 assert(app.includes('/api/assessments/offline'));
 assert(app.includes('/api/injury/episodes'));
 assert(app.includes('/api/bookings'));
});

test('beta 1.5 Android installs alongside beta 1.4.2 with an independent package ID',()=>{
 const c=buildConfig('debug');
 assert.equal(c.appId,'com.winmindperform.spps.beta15');
 assert.equal(c.appName,'SPPS Free 1.0');
 const gradle=readFileSync('android/app/build.gradle','utf8');
 assert.match(gradle,/applicationId "com\.winmindperform\.spps\.beta15"/);
 assert.match(gradle,/namespace = "com\.winmindperform\.spps"/);
});
test('SDK bootstrap is local to this project and keeps existing configuration',()=>{
 const script=readFileSync('tools/Configure-AndroidSDK.ps1','utf8');
 assert.match(script,/local\.properties/);
 assert.match(script,/if \(Test-Path \$propertiesFile\)/);
 assert.match(script,/Android SDK Platform 36 not found/);
});
test('private practitioner session is cleared on logout and session expiry',()=>{
 const app=readFileSync('web/app.js','utf8');
 assert.match(app,/function clearPrivateState\(\)/);
 assert.match(app,/if\(a==='logout'\).*clearPrivateState\(\)/);
 assert.match(app,/r\.status===401 && state\.user/);
});
test('UI controls retain submit text and use local calendar date',()=>{
 const app=readFileSync('web/app.js','utf8');
 assert.match(app,/submitter\.textContent=originalText/);
 assert.match(app,/form\.dataset\.submitting==='true'/);
 assert.match(app,/d\.getMonth\(\)\+1/);
});

test('native asset copy and Android branding match the canonical beta 1.5 config',()=>{
 const staged=JSON.parse(readFileSync('android/app/src/main/assets/capacitor.config.json','utf8'));
 const source=buildConfig('debug');
 assert.equal(staged.appId,source.appId);
 assert.equal(staged.appName,source.appName);
 const patch=readFileSync('scripts/apply-native-overrides.mjs','utf8');
 assert.match(patch,/SPPS Free 1\.0/);
 assert.equal(readFileSync('android/app/src/main/assets/public/assets/app.js','utf8'),readFileSync('web/app.js','utf8'));
});
