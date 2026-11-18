import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const web=readFileSync('web/app.js', 'utf8');
const android=readFileSync('www/assets/app.js','utf8');
const backend=readFileSync('server/development_profile.py','utf8');
const localPolicies=readFileSync('migrations/0003_local_clinical_parity.sql','utf8');

test('Android and browser use byte-identical frontend behavior',()=>{
  assert.equal(web,android);
});
test('original full navigation remains in source, not silently removed',()=>{
  for(const label of ['Overview','Enquiries','Bookings & Revenue','Athletes & Cases','Assessments & Trends','Monitoring','Load & Recovery','Injury Psychology','Interventions','Reports','Profile & Privacy']){
    assert.ok(web.includes(label),label);
  }
});
test('clinical profile exposes core sections but holds marketplace and billing',()=>{
  assert.match(web,/allowedViews=clinical\?\['overview','athletes','assessments','monitoring','analytics','injury','interventions','reports'\]/);
  assert.match(web,/clinical_parity/);
  assert.match(backend,/SPPS_DEV_CLINICAL_PARITY/);
  assert.match(backend,/CLINICAL_WRITES/);
});
test('local SQL uses constrained grants and owner checks, not bypass privileges',()=>{
  assert.match(localPolicies,/CREATE POLICY own_note_insert/);
  assert.match(localPolicies,/CREATE POLICY own_assignment_insert/);
  assert.match(localPolicies,/CREATE POLICY own_report_insert/);
  assert.match(localPolicies,/CREATE POLICY own_injury_insert/);
  assert.match(localPolicies,/CREATE POLICY own_checkin_insert/);
  assert.doesNotMatch(localPolicies,/BYPASSRLS|DISABLE ROW LEVEL SECURITY|GRANT ALL/i);
});
