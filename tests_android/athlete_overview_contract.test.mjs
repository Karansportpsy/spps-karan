import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const app=readFileSync('web/app.js','utf8');
const styles=readFileSync('web/styles.css','utf8');

test('newly created fictional athlete becomes the active overview, not the first alphabetically',()=>{
  assert.match(app,/state\.athleteId=created\.id;await loadAll\(\);state\.view='athletes'/);
});
test('athlete core detail renders before optional histories and does not fail closed for optional API errors',()=>{
  assert.match(app,/const detail=await api\(`\/api\/athletes\/\$\{selectedId\}`\)/);
  assert.match(app,/render\(\); \/\/ Show athlete overview immediately/);
  assert.match(app,/await Promise\.allSettled\(paths\.map/);
  assert.match(app,/Athlete overview opened; some history sections could not load/);
});
test('assessments expose both athlete and case selection and case creation',()=>{
  assert.match(app,/data-action="athlete-select"/);
  assert.match(app,/data-action="case-select"/);
  assert.match(app,/button\('\+ Create case','new-case'/);
  assert.match(app,/caseId=Number\(e\.target\.value\)/);
});
test('athlete overview is labelled, navigable and mobile scrolls to selected athlete',()=>{
  assert.match(app,/id="athlete-overview-panel" aria-label="Athlete overview"/);
  assert.match(app,/data-action="athlete" data-id="\$\{x\.id\}" aria-label="Open overview for/);
  assert.match(app,/scrollIntoView\(\{block:'start',behavior:'smooth'\}\)/);
  assert.match(styles,/#athlete-overview-panel \{scroll-margin-top:118px;\}/);
});
