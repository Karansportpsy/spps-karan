// Stage 13A.8: athlete/guardian portal ships identically to web and Android (www/).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = p => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('portal assets are byte-identical in the Capacitor webDir', () => {
  assert.equal(read('www/portal.html'), read('web/portal.html'));
  assert.equal(read('www/assets/portal.js'), read('web/portal.js'));
});

test('portal page has no inline script and loads only same-origin assets', () => {
  const html = read('web/portal.html');
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i);
  assert.doesNotMatch(html, /https?:\/\//);
  assert.match(html, /src="\/assets\/portal\.js/);
});

test('portal never persists session secrets in browser storage', () => {
  const js = read('web/portal.js');
  assert.doesNotMatch(js, /localStorage\.setItem|sessionStorage|document\.cookie|indexedDB/);
  assert.match(js, /credentials: 'same-origin'/);
  assert.match(js, /'X-CSRF-Token'/);
});

test('portal escapes server text before rendering', () => {
  const js = read('web/portal.js');
  assert.match(js, /const esc = /);
  for (const field of ['practitioner_name', 'display_name', 'evidence_reference', 'guardian_email']) {
    assert.doesNotMatch(js, new RegExp('\$\{(?:[a-z]+\.)' + field + '\}'), `${field} rendered without esc()`);
  }
});

test('practitioner workspace exposes portal access only when the backend enables it', () => {
  const app = read('web/app.js');
  assert.match(app, /state\.portalEnabled\?button\("Portal access","portal-access"/);
  assert.match(app, /state\.portalEnabled=Boolean\(runtime\.portal\)/);
  assert.equal(read('www/assets/app.js'), app);
});

test('practitioner review controls are administrator-only and state the three publication gates', () => {
  const app = read('web/app.js');
  assert.match(app, /state\.user\?\.is_admin\?button\('Practitioner reviews','practitioner-reviews'/);
  assert.match(app, /Payment alone never publishes a profile/);
  assert.match(app, /another administrator must review/);
});
