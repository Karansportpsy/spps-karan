import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildConfig} from '../scripts/configure-android.mjs';

const code=readFileSync('scripts/apply-native-overrides.mjs','utf8');
test('v1.5 hosted identity isolated from v1.4.2',()=>{
 const cfg=buildConfig('hosted-beta','https://pilot.example.test');
 assert.equal(cfg.appId,'com.winmindperform.spps.beta15');
 assert.equal(cfg.appName,'SPPS Free 1.0');
 assert.equal(cfg.server.url,'https://pilot.example.test/studio');
 assert.doesNotMatch(cfg.server.url,/localhost/);
});
test('v1.5 never accepts insecure hosted origin',()=>{
 for(const s of ['http://pilot.example.test','https://localhost','https://pilot.example.test/secret','https://user:password@pilot.example.test']){
  assert.throws(()=>buildConfig('hosted-beta',s));
 }
});
test('hosted debug builds disable Android cleartext',()=>{
 assert.match(code,/hostedMode/);
 assert.match(code,/hostedMode\?'false':'true'/);
});
test('staging rollout is locked until explicitly opened',()=>{
 const main=readFileSync('server/main.py','utf8');
 assert.match(main,/MODEL_C_HOSTED_PILOT_UNLOCKED/);
 assert.match(main,/MODEL_C_ALLOWED_HOSTS/);
 assert.match(main,/MODEL_C_INIT_DB/);
 assert.match(main,/health\/ready/);
});
