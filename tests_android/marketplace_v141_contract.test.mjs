import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildConfig} from '../scripts/configure-android.mjs';

const web=readFileSync('web/app.js','utf8');
const server=readFileSync('server/marketplace_routes.py','utf8');

test('beta 1.4.1 has separate Android app ID and retains hosted HTTPS guard',()=>{
 const debug=buildConfig('debug');
 assert.equal(debug.appId,'com.winmindperform.spps.beta15');
 assert.equal(debug.appName,'SPPS Free 1.0');
 assert.throws(()=>buildConfig('hosted-beta','http://example.org'));
});
test('marketplace UI clearly separates programme enquiry from free appointment booking',()=>{
 for(const phrase of ['My public profile','Incoming booking requests','Published appointment slots','Enquire about programme','Request annual quote']){
  assert(web.includes(phrase),phrase);
 }
 assert.match(web,/programme.*not.*payment|no charge|not charged/i);
 for(const route of ['/api/public/programmes','/api/practitioner/programmes','/api/public/organisation-quote','/api/public/programme-enquiries']){
  assert(server.includes(route),route);
 }
});
