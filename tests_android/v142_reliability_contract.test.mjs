import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {buildConfig} from '../scripts/configure-android.mjs';

const room=readFileSync('web/room.js','utf8');
const html=readFileSync('web/room.html','utf8');
const app=readFileSync('web/app.js','utf8');
const pkg=JSON.parse(readFileSync('package.json','utf8'));

test('v1.4.2 keeps independent Android application ID and secured Capacitor version intent',()=>{
 const c=buildConfig('debug');
 assert.equal(c.appId,'com.winmindperform.spps.beta15');
 assert.equal(c.appName,'SPPS Free 1.0');
 assert.equal(pkg.dependencies['@capacitor/core'],'8.6.0');
 assert.equal(pkg.dependencies['@capacitor/android'],'8.6.0');
 assert.equal(pkg.devDependencies['@capacitor/cli'],'8.6.0');
 assert.equal(c.server.url,'http://localhost:8000/studio');
});
test('diagnostic stages distinguish signaling, transport, remote track, decoded frames and playback',()=>{
 for(const phase of ['invitation','consent','local','signaling','participant','negotiation','transport','ice','peer','remoteTrack','videoFrames','inboundVideo','inboundAudio','playback']){
  assert.match(html,new RegExp('id="phase-'+phase+'"'));
 }
 for(const api of ['getStats','framesDecoded','bytesReceived','oniceconnectionstatechange','onconnectionstatechange','getUserMedia','playRemote'])assert(room.includes(api),api);
 assert.match(html,/id="copy-diagnostics"/);
 assert.match(html,/id="play-remote"/);
 assert.match(room,/No STUN\/TURN configured/);
});
test('redacted diagnostics use only allowlisted phases, never token, SDP, or candidate data',()=>{
 assert.match(room,/const phaseNames=Object\.keys\(phases\)/);
 assert.match(room,/phaseNames\.map\(key=>/);
 assert.match(room,/diagnosticSummary\(\)/);
 const copyBody=room.split('function diagnosticSummary(){')[1].split("el('copy-diagnostics').onclick")[0];
 assert.doesNotMatch(copyBody,/token|candidate|localDescription|remoteDescription|JSON\.stringify/);
 assert.doesNotMatch(html,/data-access=|data-token=/);
});
test('institutional inbox refreshes on navigation and supports manual action without changing admin access',()=>{
 assert.match(app,/if\(state\.view==='settings'\)await refreshOrganisationQuotes\(\)/);
 assert.match(app,/if\(a==='refresh-organisation-quotes'\)/);
 assert.match(app,/state\.user\?\.is_admin\?await api\('\/api\/admin\/organisation-quotes'\)/);
 assert.match(app,/new institutional quotation enquiry received/);
 assert.match(app,/Refresh enquiries/);
});
test('setup and build scripts block APK creation on npm high/critical security advisory',()=>{
 for(const file of ['setup_android_windows.bat','run_android_debug_windows.bat']){
  const contents=readFileSync(file,'utf8');
  assert.match(contents,/npm audit --audit-level=high/);
  assert.match(contents,/if errorlevel 1/);
 }
 if(existsSync('package-lock.json')){
  const lock=JSON.parse(readFileSync('package-lock.json','utf8'));
  assert.equal(lock.packages[''].dependencies['@capacitor/core'],'8.6.0');
  assert.equal(lock.packages[''].dependencies['@capacitor/android'],'8.6.0');
  assert.equal(lock.packages[''].devDependencies['@capacitor/cli'],'8.6.0');
 } // Source-only ZIP has no lock; the Windows script generates and audits one.

});
