// RC physical validation build installs beside Beta 1.5 and the M1.2 validation app.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = p => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('three distinct package identities: release, M1.2 debug and RC validation', () => {
  const gradle = read('android/app/build.gradle');
  assert.match(gradle, /applicationId "com\.winmindperform\.spps\.beta15"/);
  assert.match(gradle, /applicationIdSuffix "\.m12validation"/);
  assert.match(gradle, /rcvalidation \{\s*initWith debug\s*applicationIdSuffix "\.rcvalidation"/);
  const strings = read('android/app/src/rcvalidation/res/values/strings.xml');
  assert.match(strings, /SPPS RC Validation/);
  assert.match(strings, /com\.winmindperform\.spps\.beta15\.rcvalidation/);
});

test('RC validation cleartext exception is generated for localhost-only adb reverse', () => {
  const overrides = read('scripts/apply-native-overrides.mjs');
  assert.match(overrides, /for\(const variant of \['debug','rcvalidation'\]\)/);
  assert.match(overrides, /hostedMode\?'false':'true'/);
});
