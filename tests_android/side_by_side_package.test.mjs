import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const gradle = readFileSync('android/app/build.gradle','utf8');
const debugNames = readFileSync('android/app/src/debug/res/values/strings.xml','utf8');
const apiApp = readFileSync('web/app.js','utf8');
const androidApp = readFileSync('www/assets/app.js','utf8');
test('side-by-side Android validation package will not overwrite Beta 1.5',()=>{
 assert.match(gradle,/applicationId "com\.winmindperform\.spps\.beta15"/);
 assert.match(gradle,/applicationIdSuffix "\.m12validation"/);
 assert.match(debugNames,/SPPS Free 1\.0/);
 assert.equal(apiApp,androidApp);
});
