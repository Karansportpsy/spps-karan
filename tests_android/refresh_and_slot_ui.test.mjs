import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const js=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const api=readFileSync(new URL('../server/beta_routes.py',import.meta.url),'utf8');
test('athlete and booking navigation explicitly fetches fresh backend data',()=>{
 assert.match(js,/includes\(state\.view\)\)await loadAll\(\)/);
 assert.match(js,/if\(a==='refresh-data'\)\{await loadAll\(\)/);
});
test('appointments are listed separately from bookings, with owner-only API',()=>{
 assert.match(js,/Published appointment slots/);
 assert.match(js,/state\.slots=await api\('\/api\/slots'\)/);
 assert.match(api,/@app\.get\('\/api\/slots'\)/);
 assert.match(api,/AppointmentSlot\.practitioner_id==user\.id/);
});
