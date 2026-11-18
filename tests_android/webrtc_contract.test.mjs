import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync('web/room.js','utf8');
const room=readFileSync('web/room.html','utf8');
const manifest=readFileSync('android/app/src/main/AndroidManifest.xml','utf8');
test('early remote ICE is queued until remote description exists',()=>{
 assert.match(source,/pendingRemoteIce/);
 assert.match(source,/if\(peer\.remoteDescription\)/);
 assert.match(source,/await flushRemoteIce\(\)/);
});
test('room client keeps browser media ephemeral and ends call',()=>{
 assert.match(source,/getUserMedia/);
 assert.match(source,/\.getTracks\(\)\.forEach\(track=>track\.stop\(\)\)/);
 assert.match(source,/remote\.srcObject=null/);
 assert.match(source,/role==='practitioner'/);
 assert.match(room,/id="room-diagnostics"/);
});
test('Android source disables app data backup',()=>{
 assert.match(manifest,/android:allowBackup="false"/);
});
test('video links remain private and copied from the current app source',()=>{
 const app=readFileSync('web/app.js','utf8');
 assert.match(app,/Open practitioner room/);
 assert.match(app,/Copy guest invitation/);
 assert.match(app,/Share privately/);
 assert.doesNotMatch(app,/data-action="copy-room-invite"[^>]*>Copy guest invitation<\/button><p>Guest/);
});

test('consultation room restores the three canonical themes',()=>{
 assert.match(source,/localStorage\.getItem\('modelc-theme'\)/);
 assert.match(source,/\['original','light','dark'\]/);
 assert.match(room,/viewport-fit=cover/);
 assert.match(room,/class="room-page"/);
});
test('peer disconnection releases media and allows a manual restart',()=>{
 assert.match(source,/message\.type==='peer-left'/);
 assert.match(source,/closeMedia\(\)/);
 assert.match(source,/Start camera to retry/);
 const backend=readFileSync('server/beta_routes.py','utf8');
 assert.match(backend,/peers\[room_id\]\[role\]=websocket\s+try:\s+await websocket\.accept\(\)/);
 assert.match(backend,/send_json\(\{'type':'peer-left'\}\)/);
});
