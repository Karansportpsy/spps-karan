import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const app = readFileSync('web/app.js', 'utf8');
const debug = readFileSync('run_android_debug_windows.bat', 'utf8');

test('clinical API GETs bypass WebView response cache', () => {
  assert.match(app, /credentials:'same-origin',cache:'no-store'/);
});

test('both clinical POSTs verify the assigned ID through authenticated GET', () => {
  assert.match(app, /async function verifyClinicalWrite\(kind,athleteId,created\)/);
  assert.match(app, /records\.some\(row=>row\.id===created\.id\)/);
  assert.match(app, /verifyClinicalWrite\('assessment',athleteId,saved\)/);
  assert.match(app, /verifyClinicalWrite\('monitoring',athleteId,saved\)/);
  assert.match(app, /Refresh before retrying/);
});

test('monitoring form is bound to the visible athlete and its case list', () => {
  assert.match(app, /type="hidden" name="athlete_id" value=/);
  assert.match(app, /Athlete \(change selection on the Monitoring page\)/);
});

test('assessment submission preserves selected athlete and case', () => {
  assert.match(app, /data-form="offline-assessment" data-athlete-id=/);
  assert.match(app, /const athleteId=Number\(form\.dataset\.athleteId\),caseId=Number\(form\.dataset\.caseId\)/);
});

test('assessment and monitoring offer explicit athlete scope and manual refresh', () => {
  assert.match(app, /function clinicalAthletePicker\(\)/);
  assert.match(app, /data-action="athlete-select"/);
  assert.equal((app.match(/'refresh-clinical'/g)||[]).length>=3, true);
});

test('Windows Android launcher can forward debug WebView to 8001 backend without losing the mapping', () => {
  assert.match(debug, /SPPS_BACKEND_PORT/);
  assert.match(debug, /adb reverse tcp:8000 tcp:%SPPS_BACKEND_PORT%/);
});

