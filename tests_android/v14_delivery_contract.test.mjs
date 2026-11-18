import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildConfig} from '../scripts/configure-android.mjs';

const ui=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const routes=readFileSync(new URL('../server/beta_routes.py',import.meta.url),'utf8');
const css=readFileSync(new URL('../web/styles.css',import.meta.url),'utf8');
test('beta 1.4 has own Android package and remains local by default',()=>{
  const debug=buildConfig('debug');
  assert.equal(debug.appId,'com.winmindperform.spps.beta15');
  assert.equal(debug.server.url,'http://localhost:8000/studio');
  assert.equal(debug.server.cleartext,true);
  assert.equal(buildConfig('bundle').server.url,undefined);
});
test('hosted beta requires HTTPS origin, never insecure LAN or loopback',()=>{
  assert.throws(()=>buildConfig('hosted-beta','http://public.example.com'));
  assert.throws(()=>buildConfig('hosted-beta','https://localhost'));
  assert.throws(()=>buildConfig('hosted-beta','https://user:password@example.org'));
  assert.throws(()=>buildConfig('hosted-beta','https://example.org/other'));
  const safe=buildConfig('hosted-beta','https://spps.example.org');
  assert.equal(safe.server.url,'https://spps.example.org/studio');
  assert.equal(safe.server.cleartext,undefined);
});
test('web frontend shows deliberate consultation completion and warning',()=>{
  assert.match(ui,/Mark completed/);
  assert.match(ui,/data-action="complete-booking"|complete-booking','btn small'/);
  assert.match(ui,/\/api\/bookings\/\$\{el\.dataset\.id\}\/complete/);
  assert.match(routes,/room\.active=False/);
  assert.match(routes,/access\.revoked=True/);
});

test('touch layout preserves 44px targets, safe-area styling and reduced motion',()=>{
  assert.match(css,/min-height:44px/);
  assert.match(css,/prefers-reduced-motion/);
  assert.match(css,/spps-safe-bottom/);
  assert.match(css,/font-size:16px/);
});
