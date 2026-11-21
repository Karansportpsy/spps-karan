/* SPPS v2 screens. Loaded after app.js; uses its helpers (state, api, render, pageHead, button, badge, esc, when,
   openDialog, closeDialog, notify) and the existing CSS classes only, so the look is unchanged.
   Every feature that is behind a flag checks state.v2flags (from /api/runtime) before showing anything. */
(function(){
'use strict';
const H=window.SPPSHost;if(!H)return;
const {state,api,render,pageHead,button,badge,esc,when,openDialog,closeDialog,notify,loadAll}=H;
const V2={};
const flag=n=>Boolean(state.v2flags&&state.v2flags[n]);
const num=v=>Number(v)||0;
const fmtDate=v=>v?esc(String(v).slice(0,10)):'—';

V2.load=async function(){
 if(!state.user)return;
 try{state.v2settings=await api('/api/v2/settings')}catch{state.v2settings={ai_available:false}}
 try{state.v2exports=await api('/api/v2/account/exports')}catch{state.v2exports=[]}
 if(flag('SPPS_PRACTITIONER_CLOSURE_ENABLED')){try{state.v2closure=await api('/api/v2/account/closure')}catch{state.v2closure=null}}
 if(state.user.is_admin){
  try{state.v2accessCounts=await api('/api/v2/admin/access-counts')}catch{state.v2accessCounts=[]}
  if(flag('SPPS_PRACTITIONER_CLOSURE_ENABLED')){try{state.v2closures=await api('/api/v2/admin/closures')}catch{state.v2closures=[]}}
 }
 for(const fn of V2.loaders)await fn();
};
V2.loaders=[];

// ---------------------------------------------------------------- athlete overview buttons
V2.athleteButtons=function(a){
 if(!a)return '';
 return button('Access log','v2:access-log','btn small',`data-id="${num(a.id)}"`)+V2.extraAthleteButtons.map(f=>f(a)).join('');
};
V2.extraAthleteButtons=[];

// ---------------------------------------------------------------- Profile & Privacy cards
V2.settingsCards=function(){
 const exp=state.v2exports||[];
 let html=`<section class="card" data-v2="your-data"><h3>Your data</h3><p>Where your records are stored, who can see them, and what SPPS never does with them.</p><p><a class="btn" href="/your-data" target="_blank" rel="noopener">Read “Your data”</a></p></section>`+
 `<section class="card" data-v2="export"><h3>Download all my data</h3><p class="small muted">One ZIP file with your profile, athletes, cases, notes, assessments and reports (JSON and spreadsheets). It contains confidential client information: store it securely.</p><form data-form="v2:export" class="stack"><div class="field"><label>Confirm your password<input name="password" type="password" autocomplete="current-password"></label></div><button class="btn primary" type="submit">Download ZIP</button></form>${exp.length?`<p class="small muted">Previous downloads: ${exp.slice(0,5).map(e=>fmtDate(e.created_at)).join(', ')}</p>`:''}</section>`;
 if(state.v2settings&&state.v2settings.ai_available){
  html+=`<section class="card" data-v2="ai"><h3>Drafting assistance (optional)</h3><p class="small">${esc(state.v2settings.ai_notice||'')}</p>${button(state.v2settings.ai_opt_in?'Turn off':'Turn on','v2:ai-toggle','btn',`data-on="${state.v2settings.ai_opt_in?'0':'1'}"`)}</section>`;
 }
 if(flag('SPPS_PRACTITIONER_CLOSURE_ENABLED')&&state.v2closure&&!state.user?.is_admin)html+=closureCard(state.v2closure);
 return html+V2.extraSettingsCards.map(f=>f()).join('');
};
V2.extraSettingsCards=[];

function closureCard(c){
 if(c.state==='none')return `<section class="card" data-v2="closure"><h3>Close my account</h3><p class="small muted">Closing gives you a notice period of ${'30'} days to decide what happens to each athlete record. You can cancel at any time during that period.</p><form data-form="v2:closure-start" class="stack"><div class="field"><label>Confirm your password<input name="password" type="password" autocomplete="current-password"></label></div><button class="btn danger" type="submit">Start closing my account</button></form></section>`;
 const opts=d=>['export_and_erase','keep_deactivated'].map(v=>`<option value="${v}" ${d.disposition===v?'selected':''}>${v==='export_and_erase'?'Export, then erase':'Keep (account deactivated)'}</option>`).join('');
 return `<section class="card" data-v2="closure"><h3>Closing your account ${badge('In progress','gold')}</h3><p>Notice period ends ${fmtDate(c.notice_ends_at)}.</p>`+
  `${(c.dispositions||[]).map(d=>`<form data-form="v2:closure-disposition" class="inline-actions" data-athlete="${num(d.athlete_id)}"><span>${esc(d.display_name)}</span><select name="disposition"><option value="">Choose…</option>${opts(d)}</select><button class="btn small" type="submit">Save</button></form>`).join('')||'<p class="small muted">You hold no athlete records.</p>'}`+
  `${(c.blockers||[]).length?`<div class="strip">${c.blockers.map(esc).join(' · ')}</div>`:''}<div class="inline-actions">${button('Complete closure','v2:closure-complete','btn danger'+(c.can_complete?'':' disabled'),c.can_complete?'':'disabled')}${button('Cancel closure','v2:closure-cancel','btn')}</div></section>`;
}

// ---------------------------------------------------------------- Admin Dashboard cards (counts only)
V2.adminCards=function(){
 const rows=state.v2accessCounts||[];
 let html=`<section class="card" data-v2="access-counts"><h2>Record access (counts only)</h2><p class="small muted">How often each practitioner opened or changed their own athlete records. You cannot see the records themselves.</p>${rows.map(r=>`<div class="strip"><b>${esc(r.name||('#'+r.practitioner_id))}</b> · ${num(r.reads)} reads · ${num(r.writes)} writes · ${num(r.exports)} exports</div>`).join('')||'<p class="small muted">No activity yet.</p>'}</section>`;
 if(flag('SPPS_PRACTITIONER_CLOSURE_ENABLED')){
  const cl=state.v2closures||[];
  html+=`<section class="card" data-v2="closures"><h2>Account closures</h2>${cl.map(c=>`<div class="strip"><b>${esc(c.archive?.practitioner?.name||('#'+c.practitioner_id))}</b> · ${esc(c.state)} · requested ${fmtDate(c.requested_at)}${c.cooling_off_ends_at?` · cooling-off until ${fmtDate(c.cooling_off_ends_at)}`:''} ${c.state==='closed'?button('Restore','v2:closure-restore','btn small',`data-id="${num(c.id)}"`)+button('Purge erased records','v2:closure-purge','btn small danger',`data-id="${num(c.id)}"`):''}</div>`).join('')||'<p class="small muted">No closures.</p>'}</section>`;
 }
 return html+V2.extraAdminCards.map(f=>f()).join('');
};
V2.extraAdminCards=[];

// ---------------------------------------------------------------- actions and forms
V2.actions={
 'v2:access-log':async el=>{
  const rows=await api(`/api/v2/athletes/${num(el.dataset.id)}/access-log`);
  openDialog('Access log',`<div class="data-table-wrap"><table class="data-table"><thead><tr><th>When</th><th>Action</th><th>What</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(when(r.at))}</td><td>${esc(r.action)}</td><td class="small">${esc(r.resource)}</td></tr>`).join('')||'<tr><td colspan="3">No entries.</td></tr>'}</tbody></table></div>`,'Every opening or change of this athlete record is listed. The log never contains what the record says.');
 },
 'v2:ai-toggle':async el=>{await api('/api/v2/settings/ai',{method:'POST',body:{opt_in:el.dataset.on==='1'}});state.v2settings=await api('/api/v2/settings');render()},
 'v2:closure-complete':async()=>{state.v2closure=await api('/api/v2/account/closure/complete',{method:'POST',body:{}});notify('Your account is closed.');location.assign('/studio')},
 'v2:closure-cancel':async()=>{await api('/api/v2/account/closure/cancel',{method:'POST',body:{}});state.v2closure=await api('/api/v2/account/closure');render();notify('Closure cancelled')},
 'v2:closure-restore':async el=>{await api(`/api/v2/admin/closures/${num(el.dataset.id)}/restore`,{method:'POST',body:{}});state.v2closures=await api('/api/v2/admin/closures');render()},
 'v2:closure-purge':async el=>{if(!confirmText('Erase the records this practitioner chose to erase? This cannot be undone.'))return;const r=await api(`/api/v2/admin/closures/${num(el.dataset.id)}/purge`,{method:'POST',body:{}});state.v2closures=await api('/api/v2/admin/closures');render();notify(`${num(r.erased_records)} record(s) erased`)},
};
function confirmText(t){return window.confirm?window.confirm(t):true}
V2.forms={
 'v2:export':async(form,raw)=>{
  const res=await fetch('/api/v2/account/export',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({password:raw('password')||null})});
  if(!res.ok){let msg='Export failed';try{msg=(await res.json()).detail||msg}catch{}throw Error(msg)}
  const blob=await res.blob();const url=URL.createObjectURL(blob);const link=document.createElement('a');
  link.href=url;link.download=(res.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/)?.[1]||'spps-export.zip';
  document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
  state.v2exports=await api('/api/v2/account/exports');render();notify('Export downloaded. Keep it secure.');
 },
 'v2:closure-start':async(form,raw)=>{state.v2closure=await api('/api/v2/account/closure/start',{method:'POST',body:{password:raw('password')||null}});render()},
 'v2:closure-disposition':async(form,raw)=>{if(!raw('disposition'))throw Error('Choose an option');state.v2closure=await api('/api/v2/account/closure/dispositions',{method:'POST',body:{athlete_id:num(form.dataset.athlete),disposition:raw('disposition')}});render()},
};
V2.action=async function(a,el,evt){const fn=V2.actions[a];if(!fn)throw Error('Unknown action');await fn(el,evt)};
V2.submit=async function(type,form,raw){const fn=V2.forms[type];if(!fn)throw Error('Unknown form');await fn(form,raw)};

// ---------------------------------------------------------------- extra workspace views (later phases)
V2.views={};
V2.nav=[];
V2.page=function(view){const fn=V2.views[view];return fn?fn():null};

// ================================================================ Phase 3: the practitioner record
V2.extraAthleteButtons.push(a=>button('Profile','v2:profile','btn small',`data-id="${num(a.id)}"`)+
 button('Timeline','v2:timeline','btn small',`data-id="${num(a.id)}"`)+
 button('Formulation (5Ps)','v2:formulation','btn small',`data-id="${num(a.id)}"`)+
 button('Write note','v2:compose','btn small primary',`data-id="${num(a.id)}"`));

const LEVELS=['not specified','recreational','school','club','state','national','international','professional'];
const AGES=['not specified','under-12','12-14','15-17','18-24','25-34','35+'];
const sel=(name,values,current)=>`<select name="${name}">${values.map(v=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(v)}</option>`).join('')}</select>`;
const field=(label,inner)=>`<div class="field"><label>${label}${inner}</label></div>`;

V2.actions['v2:profile']=async el=>{
 const p=await api(`/api/v2/athletes/${num(el.dataset.id)}/profile`);
 openDialog('Athlete profile',`<form data-form="v2:profile" data-id="${num(p.athlete_id)}" class="stack"><div class="field-grid">`+
  field('Level',sel('level',LEVELS,p.level))+field('Age group',sel('age_group',AGES,p.age_group))+
  field('Consent',sel('consent_status',['not_recorded','recorded','withdrawn'],p.consent_status))+
  field('Referral source',`<input name="referral_source" maxlength="120" value="${esc(p.referral_source)}">`)+
  field('Guardian name (required under 18)',`<input name="guardian_name" maxlength="120" value="${esc(p.guardian_name)}">`)+
  field('Guardian email',`<input name="guardian_email" type="email" maxlength="250" value="${esc(p.guardian_email)}">`)+
  field('Guardian phone',`<input name="guardian_phone" maxlength="24" value="${esc(p.guardian_phone)}">`)+
  `</div>`+field('Referral notes',`<textarea name="referral_notes" rows="3" maxlength="4000">${esc(p.referral_notes)}</textarea>`)+
  `<div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Save profile</button></div></form>`,
  `${esc(p.display_name)} · ${esc(p.code)} · ${esc(p.sport)}`);
};
V2.forms['v2:profile']=async(form,raw)=>{
 await api(`/api/v2/athletes/${num(form.dataset.id)}/profile`,{method:'PUT',body:{level:raw('level'),age_group:raw('age_group'),
  consent_status:raw('consent_status'),referral_source:raw('referral_source'),guardian_name:raw('guardian_name'),
  guardian_email:raw('guardian_email'),guardian_phone:raw('guardian_phone'),referral_notes:raw('referral_notes')}});
 closeDialog();notify('Profile saved');
};

const KIND_LABEL={note:'Note',amendment:'Amendment',observation:'Measure',assessment:'Assessment',intervention:'Intervention',report:'Report',formulation:'Formulation',flag:'Flag',checkin:'Check-in',assignment:'Assigned'};
V2.actions['v2:timeline']=async el=>{
 const rows=await api(`/api/v2/athletes/${num(el.dataset.id)}/timeline`);
 openDialog('Timeline',rows.map(x=>`<div class="strip" data-timeline-kind="${esc(x.kind)}"><b>${fmtDate(x.date)}</b> · ${badge(KIND_LABEL[x.kind]||x.kind)} ${esc(x.title||'')}${x.value!==undefined?` · ${esc(String(x.value))} ${esc(x.unit||'')}`:''}${x.state?` · ${esc(x.state)}`:''}${x.kind==='note'?' '+button('Open','v2:note','btn small',`data-id="${num(x.id)}"`):''}${x.text?`<p class="small">${esc(x.text)}</p>`:''}</div>`).join('')||'<p class="small muted">Nothing recorded yet.</p>','Everything recorded for this athlete, newest first. Missing dates are not filled in.');
};

V2.actions['v2:note']=async el=>{
 const n=await api(`/api/v2/notes/${num(el.dataset.id)}`);
 openDialog('Consultation note',`<p class="small muted">${fmtDate(n.note_date)} · ${n.locked?badge('Signed and locked','good'):badge('Draft','gold')}${n.integrity===false?' '+badge('Changed after signing','gold'):''}</p><div class="strip" style="white-space:pre-wrap">${esc(n.content)}</div>`+
  (n.amendments||[]).map(x=>`<div class="strip"><b>Amendment ${fmtDate(x.created_at)}</b> · ${esc(x.reason)}<p style="white-space:pre-wrap">${esc(x.content)}</p></div>`).join('')+
  (n.locked?`<form data-form="v2:amend" data-id="${num(n.id)}" class="stack">${field('Amendment',`<textarea name="content" rows="3" required minlength="2" maxlength="16000"></textarea>`)}${field('Reason',`<input name="reason" required minlength="3" maxlength="240">`)}<button class="btn" type="submit">Add amendment</button></form>`
   :`<div class="form-actions">${button('Sign and lock','v2:sign','btn primary',`data-id="${num(n.id)}" data-revision="${num(n.revision)}"`)}</div>`),
  n.locked?'A signed note never changes. Corrections are added as dated amendments.':'Signing locks the note. After that only amendments can be added.');
};
V2.actions['v2:sign']=async el=>{await api(`/api/v2/notes/${num(el.dataset.id)}/sign`,{method:'POST',body:{expected_revision:num(el.dataset.revision)}});await V2.actions['v2:note'](el);notify('Note signed and locked')};
V2.forms['v2:amend']=async(form,raw)=>{await api(`/api/v2/notes/${num(form.dataset.id)}/amendments`,{method:'POST',body:{content:raw('content'),reason:raw('reason')}});await V2.actions['v2:note'](form);notify('Amendment added')};

// ---- note composer: server autosave, encrypted offline copy in this browser when the server cannot be reached
const OFFLINE_KEY='spps.v2.offline-drafts';
let offlineKeys={};
async function offlineCryptoKey(kid){
 if(!offlineKeys[kid||'']){const k=await api('/api/v2/offline-key'+(kid?`?kid=${encodeURIComponent(kid)}`:''));
  offlineKeys[k.kid]=await crypto.subtle.importKey('raw',Uint8Array.from(atob(k.key),c=>c.charCodeAt(0)),'AES-GCM',false,['encrypt','decrypt']);offlineKeys[k.kid].kid=k.kid;if(!kid)offlineKeys['']=offlineKeys[k.kid]}
 return offlineKeys[kid||''];
}
const b64=buf=>btoa(String.fromCharCode(...new Uint8Array(buf)));
function readOffline(){try{return JSON.parse(localStorage.getItem(OFFLINE_KEY)||'{}')}catch{return {}}}
function writeOffline(all){try{localStorage.setItem(OFFLINE_KEY,JSON.stringify(all));return true}catch{return false}}
async function saveOffline(draft){
 const key=await offlineCryptoKey();if(!key)return false;
 const iv=crypto.getRandomValues(new Uint8Array(12));
 const ct=await crypto.subtle.encrypt({name:'AES-GCM',iv},key,new TextEncoder().encode(JSON.stringify(draft)));
 const all=readOffline();all[draft.client_draft_id]={kid:key.kid,iv:b64(iv),ct:b64(ct),saved_at:Date.now()};return writeOffline(all);
}
V2.syncOffline=async function(){
 const all=readOffline();let synced=0;
 for(const [id,entry] of Object.entries(all)){
  try{const key=await offlineCryptoKey(entry.kid);
   const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:Uint8Array.from(atob(entry.iv),c=>c.charCodeAt(0))},key,Uint8Array.from(atob(entry.ct),c=>c.charCodeAt(0)));
   const d=JSON.parse(new TextDecoder().decode(plain));
   await api(`/api/v2/note-drafts/${encodeURIComponent(id)}`,{method:'PUT',body:{case_id:d.case_id,note_date:d.note_date,content:d.content,expected_revision:d.revision||0}});
   delete all[id];synced++;
  }catch(err){if(/HTTP 409/.test(err.message)){delete all[id]}}
 }
 writeOffline(all);if(synced)notify(`${synced} offline draft(s) saved to the server`);
};
V2.loaders.push(async()=>{try{await V2.syncOffline()}catch{}});
window.addEventListener('online',()=>{if(state.user)V2.syncOffline().catch(()=>{})});

let composer=null;
V2.actions['v2:compose']=async el=>{
 const aid=num(el.dataset.id);
 const [detail,templates]=await Promise.all([api(`/api/athletes/${aid}`),api('/api/v2/note-templates')]);
 const cases=detail.cases||[];
 if(!cases.length)throw Error('Open a case for this athlete first');
 composer={id:'draft-'+(crypto.randomUUID?crypto.randomUUID():String(Date.now())),revision:0,timer:null,athlete:aid};
 openDialog('Write note',`<form data-form="v2:compose" class="stack"><div class="field-grid">${field('Case',`<select name="case_id">${cases.map(c=>`<option value="${num(c.id)}">${esc(c.title)}</option>`).join('')}</select>`)}${field('Date',`<input name="note_date" type="date" required value="${new Date().toISOString().slice(0,10)}">`)}${field('Template',`<select name="template" data-v2-template><option value="">None</option>${templates.map(t=>`<option value="${num(t.id)}">${esc(t.name)}</option>`).join('')}</select>`)}</div>${field('Note',`<textarea name="content" rows="10" maxlength="16000" data-v2-autosave></textarea>`)}<p class="small muted" data-v2-save-status>Not saved yet</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Save note</button></div></form>`,'Drafts are saved to the server as you type. Sign the note afterwards to lock it.');
 const form=document.querySelector('form[data-form="v2:compose"]');
 form.querySelector('[data-v2-template]').addEventListener('change',e=>{const t=templates.find(x=>String(x.id)===e.target.value);const area=form.querySelector('[data-v2-autosave]');if(t&&!area.value.trim())area.value=t.body;schedule(form)});
 form.addEventListener('input',()=>schedule(form));
};
function schedule(form){if(!composer)return;clearTimeout(composer.timer);composer.timer=setTimeout(()=>autosave(form).catch(()=>{}),1500)}
async function autosave(form){
 const status=form.querySelector('[data-v2-save-status]');
 const d={client_draft_id:composer.id,case_id:num(form.elements.case_id.value),note_date:form.elements.note_date.value,content:form.elements.content.value,revision:composer.revision};
 try{const r=await api(`/api/v2/note-drafts/${composer.id}`,{method:'PUT',body:{case_id:d.case_id,note_date:d.note_date,content:d.content,expected_revision:composer.revision}});
  composer.revision=r.revision;if(status)status.textContent='Saved to the server';
 }catch(err){
  if(/could not be reached/.test(err.message)||navigator.onLine===false){const ok=await saveOffline(d).catch(()=>false);if(status)status.textContent=ok?'Offline: saved encrypted in this browser; it will be sent when you are back online':'Offline: could not keep a copy in this browser';}
  else if(status)status.textContent=err.message;
 }
}
V2.forms['v2:compose']=async(form)=>{
 if(!composer)throw Error('Open the note again');
 clearTimeout(composer.timer);await autosave(form);
 if(!composer.revision)throw Error('The note has not reached the server yet; it is kept and will be sent when you are online');
 const n=await api(`/api/v2/note-drafts/${composer.id}/commit`,{method:'POST',body:{}});
 composer=null;closeDialog();notify('Note saved. Open it from the timeline to sign and lock it.');
 if(state.athleteId)try{await loadAll()}catch{}
 return n;
};

V2.actions['v2:formulation']=async el=>{
 const aid=num(el.dataset.id);const f=await api(`/api/v2/athletes/${aid}/formulation`);const cur=f.current||{};
 const label={presenting:'Presenting',predisposing:'Predisposing',precipitating:'Precipitating',perpetuating:'Perpetuating',protective:'Protective'};
 openDialog('Case formulation (5Ps)',`<form data-form="v2:formulation" data-id="${aid}" data-version="${num(cur.version)}" class="stack">${f.sections.map(s=>field(label[s]||s,`<textarea name="${s}" rows="3" maxlength="6000">${esc(cur[s]||'')}</textarea>`)).join('')}<p class="small muted">${cur.version?`Version ${num(cur.version)} · ${fmtDate(cur.created_at)} · ${f.history.length} earlier version(s) kept`:'No formulation yet'}</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Save new version</button></div></form>`,'Written by you. SPPS never generates formulation text.');
};
V2.forms['v2:formulation']=async(form,raw)=>{
 await api(`/api/v2/athletes/${num(form.dataset.id)}/formulation`,{method:'POST',body:{presenting:raw('presenting'),predisposing:raw('predisposing'),precipitating:raw('precipitating'),perpetuating:raw('perpetuating'),protective:raw('protective'),expected_version:num(form.dataset.version)}});
 closeDialog();notify('Formulation saved');
};

// ---- note templates (Profile & Privacy)
V2.loaders.push(async()=>{try{state.v2templates=await api('/api/v2/note-templates')}catch{state.v2templates=[]}});
V2.extraSettingsCards.push(()=>`<section class="card" data-v2="templates"><h3>Note templates</h3>${(state.v2templates||[]).map(t=>`<div class="strip"><b>${esc(t.name)}</b> ${button('Archive','v2:template-archive','btn small',`data-id="${num(t.id)}"`)}</div>`).join('')||'<p class="small muted">No templates yet.</p>'}<form data-form="v2:template" class="stack">${field('Name',`<input name="name" required minlength="2" maxlength="120">`)}${field('Headings and prompts',`<textarea name="body" rows="4" required minlength="2" maxlength="8000" placeholder="Presenting concern:&#10;Session focus:&#10;Plan:"></textarea>`)}<button class="btn" type="submit">Add template</button></form></section>`);
V2.forms['v2:template']=async(form,raw)=>{await api('/api/v2/note-templates',{method:'POST',body:{name:raw('name'),body:raw('body')}});state.v2templates=await api('/api/v2/note-templates');render();notify('Template added')};
V2.actions['v2:template-archive']=async el=>{await api(`/api/v2/note-templates/${num(el.dataset.id)}/archive`,{method:'POST',body:{}});state.v2templates=await api('/api/v2/note-templates');render()};

// ---- import athletes from CSV / Excel: upload -> map columns -> preview -> commit (all or nothing)
V2.extraSettingsCards.push(()=>`<section class="card" data-v2="import"><h3>Import athletes</h3><p class="small muted">Upload a CSV or Excel file of your athletes. You choose which column is which, see every problem before anything is saved, and either everything is imported or nothing is.</p>${button('Import from a spreadsheet','v2:import','btn')}</section>`);
let importState=null;
V2.actions['v2:import']=async()=>{
 openDialog('Import athletes',`<form data-form="v2:import-upload" class="stack">${field('Spreadsheet (.csv or .xlsx, up to 2 MB)',`<input name="file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required>`)}<button class="btn primary" type="submit">Upload and map columns</button></form>`,'Nothing is saved until you confirm the preview.');
};
const readB64=file=>new Promise((res,rej)=>{if(file.size>2*1024*1024)return rej(Error('The file is larger than 2 MB'));const r=new FileReader();r.onerror=()=>rej(Error('The file could not be read'));r.onload=()=>res(String(r.result).split(',')[1]||'');r.readAsDataURL(file)});
V2.forms['v2:import-upload']=async(form)=>{
 const file=form.elements.file.files?.[0];if(!file)throw Error('Choose a file');
 const info=await api('/api/v2/imports',{method:'POST',body:{filename:file.name,content_base64:await readB64(file)}});
 importState=info;
 const labels={code:'Athlete ID *',display_name:'Name *',sport:'Sport *',athlete_group:'Group',level:'Level',age_group:'Age group',guardian_name:'Guardian name',guardian_email:'Guardian email',guardian_phone:'Guardian phone',consent_recorded:'Consent recorded (yes/no)',referral_source:'Referral source'};
 const opts=cur=>`<option value="">— not in file —</option>`+info.columns.map(c=>`<option value="${esc(c)}" ${c===cur?'selected':''}>${esc(c)}</option>`).join('');
 openDialog('Map columns',`<form data-form="v2:import-preview" class="stack"><p class="small muted">${num(info.rows_total)} rows found in ${esc(file.name)}.</p><div class="field-grid">${info.targets.map(t=>field(labels[t]||t,`<select name="${t}">${opts(info.suggested_mapping[t])}</select>`)).join('')}</div><div class="form-actions">${button('Cancel','v2:import-discard','btn')}<button class="btn primary" type="submit">Check every row</button></div></form><div data-v2-import-result></div>`,'Fields marked * are required. Levels: '+esc(info.levels.join(', '))+'. Age groups: '+esc(info.age_groups.join(', '))+'.');
};
function importMapping(form){const m={};for(const t of importState.targets){const v=form.elements[t]?.value;if(v)m[t]=v}return m}
V2.forms['v2:import-preview']=async(form)=>{
 const mapping=importMapping(form);const pv=await api(`/api/v2/imports/${num(importState.id)}/preview`,{method:'POST',body:{mapping}});
 importState.mapping=mapping;
 const box=document.querySelector('[data-v2-import-result]');
 box.innerHTML=pv.errors.length?`<div class="card"><h3>${num(pv.errors.length)} problem(s) — nothing will be imported until they are fixed</h3>${pv.errors.slice(0,100).map(e=>`<div class="strip">Row ${num(e.row)} · ${esc(e.field)} · ${esc(e.message)}</div>`).join('')}<p class="small muted">Correct the spreadsheet and upload it again.</p></div>`
  :`<div class="card"><h3>${num(pv.valid_rows)} athletes ready</h3>${pv.preview.slice(0,10).map(r=>`<div class="strip"><b>${esc(r.display_name)}</b> · ${esc(r.code)} · ${esc(r.sport)} · ${esc(r.level)} · ${esc(r.age_group)}</div>`).join('')}${pv.valid_rows>10?`<p class="small muted">…and ${num(pv.valid_rows-10)} more</p>`:''}${button('Import all '+num(pv.valid_rows),'v2:import-commit','btn primary')}</div>`;
};
V2.actions['v2:import-commit']=async()=>{const r=await api(`/api/v2/imports/${num(importState.id)}/commit`,{method:'POST',body:{mapping:importState.mapping}});importState=null;closeDialog();await loadAll();render();notify(`${num(r.imported)} athletes imported`)};
V2.actions['v2:import-discard']=async()=>{if(importState)await api(`/api/v2/imports/${num(importState.id)}/discard`,{method:'POST',body:{}}).catch(()=>{});importState=null;closeDialog()};

// ================================================================ Phase 4: assessments over time
V2.extraAthleteButtons.push(a=>button('Assessments over time','v2:instruments','btn small',`data-id="${num(a.id)}"`));

// Sparse-data trend: every administration is a dated point; missing scores are drawn as an open mark on the axis
// and labelled "missing"; consecutive points are joined only when no administration in between lacks a score.
function trendSvg(series,dates){
 const W=560,H=170,P=34;if(!dates.length)return '<p class="small muted">No administrations yet.</p>';
 const t=d=>new Date(d+'T00:00:00Z').getTime();const t0=t(dates[0]),t1=t(dates[dates.length-1]);
 const x=d=>dates.length===1?W/2:P+(W-2*P)*(t(d)-t0)/((t1-t0)||1);
 const y=v=>H-P-(H-2*P)*((v-series.min)/((series.max-series.min)||1));
 let segs='';const pts=series.points;
 for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i];const between=series.missing.some(m=>m>a.date&&m<b.date);if(!between)segs+=`<line x1="${x(a.date)}" y1="${y(a.score)}" x2="${x(b.date)}" y2="${y(b.score)}" stroke="var(--teal)" stroke-width="2"/>`}
 return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.label)} over time" style="width:100%;max-width:${W}px">`+
  `<line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="var(--line)"/><text x="4" y="${y(series.max)+4}" font-size="10" fill="var(--muted)">${esc(String(series.max))}</text><text x="4" y="${y(series.min)+4}" font-size="10" fill="var(--muted)">${esc(String(series.min))}</text>`+
  segs+pts.map(p=>`<circle cx="${x(p.date)}" cy="${y(p.score)}" r="5" fill="var(--teal)"><title>${esc(p.date)}: ${esc(String(p.score))}${p.band?' ('+esc(p.band)+')':''}</title></circle><text x="${x(p.date)}" y="${y(p.score)-9}" font-size="10" text-anchor="middle" fill="var(--ink)">${esc(String(p.score))}</text>`).join('')+
  series.missing.map(m=>`<circle cx="${x(m)}" cy="${H-P}" r="5" fill="none" stroke="var(--pink)" stroke-width="2"><title>${esc(m)}: missing</title></circle><text x="${x(m)}" y="${H-P+24}" font-size="9" text-anchor="middle" fill="var(--pink)">missing</text>`).join('')+
  dates.map(d=>`<text x="${x(d)}" y="${H-P+13}" font-size="9" text-anchor="middle" fill="var(--muted)">${esc(d)}</text>`).join('')+`</svg>`;
}
V2.trendSvg=trendSvg;

V2.actions['v2:instruments']=async el=>{
 const aid=num(el.dataset.id);const list=await api('/api/v2/instruments');
 openDialog('Assessments over time',`<div class="stack">${list.map(i=>`<div class="strip"><b>${esc(i.short_name||i.name)}</b> ${esc(i.name!==i.short_name?i.name:'')} ${i.score_entry_only?badge('Score entry only','gold'):badge('Own scale')} ${button('Enter scores','v2:instrument-entry','btn small primary',`data-athlete="${aid}" data-id="${num(i.id)}"`)} ${button('Trend','v2:instrument-trend','btn small',`data-athlete="${aid}" data-id="${num(i.id)}"`)}</div>`).join('')}</div>`,'Published tests are entered as subscale scores. Your own scales can be answered item by item.');
};
V2.actions['v2:instrument-entry']=async el=>{
 const inst=await api(`/api/v2/instruments/${num(el.dataset.id)}`);const aid=num(el.dataset.athlete);
 const today=new Date().toISOString().slice(0,10);
 const body=inst.score_entry_only||!inst.items.length
  ?inst.subscales.map(s=>field(`${esc(s.label)} (${s.min}–${s.max})`,`<input name="s:${esc(s.key)}" type="number" step="any" min="${s.min}" max="${s.max}">`)).join('')
  :inst.items.map(it=>field(`${num(it.position)}. ${esc(it.text||'')}`,`<input name="i:${num(it.id)}" type="number" step="1" min="${inst.response_min}" max="${inst.response_max}">`)).join('');
 openDialog(inst.short_name||inst.name,`<form data-form="v2:instrument-entry" data-athlete="${aid}" data-id="${num(inst.id)}" data-mode="${inst.score_entry_only||!inst.items.length?'s':'i'}" class="stack">${field('Date',`<input name="administered_on" type="date" required value="${today}">`)}${body}<div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Save</button></div></form>`,inst.score_entry_only?'Leave a subscale empty if it was not scored: it is shown as missing, never estimated.':'Unanswered items leave their subscale missing.');
};
V2.forms['v2:instrument-entry']=async(form)=>{
 const values={};for(const el of form.elements){if(!el.name||!/^[si]:/.test(el.name)||el.value==='')continue;values[el.name.slice(2)]=Number(el.value)}
 const payload={instrument_id:num(form.dataset.id),administered_on:form.elements.administered_on.value};
 if(form.dataset.mode==='s')payload.subscale_scores=values;else payload.responses=values;
 const r=await api(`/api/v2/athletes/${num(form.dataset.athlete)}/instrument-administrations`,{method:'POST',body:payload});
 closeDialog();notify('Saved: '+Object.entries(r.scores).map(([k,v])=>`${k} ${v.score===null?'missing':v.score}`).join(', '));
};
V2.actions['v2:instrument-trend']=async el=>{
 const t=await api(`/api/v2/athletes/${num(el.dataset.athlete)}/instrument-trends/${num(el.dataset.id)}`);
 openDialog('Trend · '+(t.instrument.short_name||t.instrument.name),t.series.map(s=>`<section class="card" data-v2-trend="${esc(s.key)}"><h3>${esc(s.label)}</h3>${trendSvg(s,t.dates)}<p class="small muted">${num(s.points.length)} scored · ${num(s.missing.length)} missing</p></section>`).join(''),'Each point is one dated administration. Gaps are not filled in or joined across.');
};

// ---- custom scale builder (Profile & Privacy)
V2.extraSettingsCards.push(()=>`<section class="card" data-v2="builder"><h3>Build your own scale</h3><p class="small muted">Define items, the answer scale, subscales (sum or mean, optional reverse-scored items) and interpretation bands. Published tests are available as subscale score entry; do not type their items here.</p>${button('New scale','v2:builder','btn')}</section>`);
V2.actions['v2:builder']=async()=>{
 openDialog('New scale',`<form data-form="v2:builder" class="stack"><div class="field-grid">${field('Name',`<input name="name" required minlength="2" maxlength="160">`)}${field('Short name',`<input name="short_name" maxlength="40">`)}${field('Lowest answer',`<input name="response_min" type="number" value="1" min="0" max="10">`)}${field('Highest answer',`<input name="response_max" type="number" value="5" min="1" max="100">`)}${field('Licence',sel('licence',['own','open'],'own'))}${field('Athletes may complete it',sel('assignable',['no','yes'],'no'))}</div>${field('Subscales — one per line: key | label | sum or mean | bands (min-max label; ...)',`<textarea name="subscales" rows="3" required placeholder="focus | Focus | sum | 2-5 Low; 6-10 High&#10;total | Total | sum"></textarea>`)}${field('Items — one per line: subscale key | item text | r (reverse-scored, optional)',`<textarea name="items" rows="5" required placeholder="focus | I can stay on task. |&#10;focus | I get distracted. | r"></textarea>`)}<div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Create scale</button></div></form>`);
};
V2.forms['v2:builder']=async(form,raw)=>{
 const subscales=raw('subscales').split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const [key,label,scoring,bands]=l.split('|').map(x=>(x||'').trim());return {key,label:label||key,scoring:scoring==='mean'?'mean':'sum',bands:(bands||'').split(';').map(b=>b.trim()).filter(Boolean).map(b=>{const m=b.match(/^(-?[\d.]+)\s*-\s*(-?[\d.]+)\s+(.+)$/);if(!m)throw Error('Band format: 2-5 Low');return {min:Number(m[1]),max:Number(m[2]),label:m[3]}})}});
 const items=raw('items').split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const [subscale,text,r]=l.split('|').map(x=>(x||'').trim());return {subscale,text,reverse_scored:r==='r'}});
 await api('/api/v2/instruments',{method:'POST',body:{name:raw('name'),short_name:raw('short_name'),licence:raw('licence'),response_min:Number(raw('response_min')),response_max:Number(raw('response_max')),subscales,items,assignable_to_athlete:raw('assignable')==='yes'}});
 closeDialog();notify('Scale created');
};

// ---- owner: licence position of the published instruments
V2.loaders.push(async()=>{if(state.user?.is_admin){try{state.v2instruments=(await api('/api/v2/instruments')).filter(i=>i.platform)}catch{state.v2instruments=[]}}});
V2.extraAdminCards.push(()=>`<section class="card" data-v2="licences"><h2>Assessment licences</h2><p class="small muted">Published tests are shipped as name and subscale score entry only, with no item text. Confirm the licensing position for each.</p>${(state.v2instruments||[]).map(i=>`<div class="strip"><b>${esc(i.short_name)}</b> · ${esc(i.name)} · ${esc(i.licence.replace(/_/g,' '))} · ${i.licence_confirmed?badge('Confirmed','good'):badge('Owner to confirm','gold')} ${i.licence_confirmed?'':button('Confirm','v2:licence-confirm','btn small',`data-id="${num(i.id)}"`)}</div>`).join('')||'<p class="small muted">No published instruments.</p>'}</section>`);
V2.actions['v2:licence-confirm']=async el=>{await api(`/api/v2/admin/instruments/${num(el.dataset.id)}/licence`,{method:'POST',body:{licence:'licensed_score_entry_only',confirmed:true}});state.v2instruments=(await api('/api/v2/instruments')).filter(i=>i.platform);render()};

// ================================================================ Phase 5: reports for coaches and the wider team
const AUD={athlete:'Athlete',guardian:'Parent / guardian',coach:'Coach',medical:'Medical / MDT'};
V2.extraAthleteButtons.push(a=>button('Reports to share','v2:reports','btn small',`data-id="${num(a.id)}"`));

V2.actions['v2:reports']=async el=>{
 const aid=num(el.dataset.id);
 const [list,templates,consents]=await Promise.all([api(`/api/v2/athletes/${aid}/v2-reports`),api('/api/v2/report-templates'),api(`/api/v2/athletes/${aid}/report-consents`)]);
 openDialog('Reports to share',
  `<h3>Reports</h3>${list.map(r=>`<div class="strip"><b>${esc(r.title)}</b> · ${esc(AUD[r.audience]||r.audience)} · ${r.status==='signed'?badge('Signed','good'):badge('Draft','gold')} ${button('Open','v2:report-open','btn small',`data-id="${num(r.id)}"`)}</div>`).join('')||'<p class="small muted">No reports yet.</p>'}`+
  (templates.length?`<form data-form="v2:report-new" data-athlete="${aid}" class="inline-actions"><select name="template_id">${templates.map(t=>`<option value="${num(t.id)}">${esc(t.name)}</option>`).join('')}</select><select name="audience">${Object.entries(AUD).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select><input name="title" required minlength="3" maxlength="140" placeholder="Title"><button class="btn small primary" type="submit">New report</button></form>`:'<p class="small muted">Create a report template in Profile &amp; Privacy first.</p>')+
  `<h3>Consent to share</h3>${consents.map(c=>`<div class="strip">${esc(AUD[c.audience]||c.audience)} · given by ${esc(c.given_by)} (${esc(c.method)}) ${fmtDate(c.recorded_at)} ${c.withdrawn_at?badge('Withdrawn'):button('Withdraw','v2:consent-withdraw','btn small',`data-id="${num(c.id)}" data-athlete="${aid}"`)}</div>`).join('')||'<p class="small muted">No consent recorded.</p>'}`+
  `<form data-form="v2:consent" data-athlete="${aid}" class="inline-actions"><select name="audience">${Object.entries(AUD).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select><select name="given_by"><option value="athlete">Given by the athlete</option><option value="guardian">Given by the guardian</option></select><select name="method"><option value="written">Written</option><option value="verbal">Verbal</option></select><button class="btn small" type="submit">Record consent</button></form>`,
  'Each report is written for one audience. Fields marked clinical-only are never included in any shared report.');
};
V2.forms['v2:report-new']=async(form,raw)=>{const r=await api(`/api/v2/athletes/${num(form.dataset.athlete)}/v2-reports`,{method:'POST',body:{template_id:num(raw('template_id')),audience:raw('audience'),title:raw('title')}});await V2.actions['v2:report-open']({dataset:{id:r.id}})};
V2.forms['v2:consent']=async(form,raw)=>{await api(`/api/v2/athletes/${num(form.dataset.athlete)}/report-consents`,{method:'POST',body:{audience:raw('audience'),given_by:raw('given_by'),method:raw('method')}});await V2.actions['v2:reports']({dataset:{id:form.dataset.athlete}});notify('Consent recorded')};
V2.actions['v2:consent-withdraw']=async el=>{await api(`/api/v2/report-consents/${num(el.dataset.id)}/withdraw`,{method:'POST',body:{}});await V2.actions['v2:reports']({dataset:{id:el.dataset.athlete}});notify('Consent withdrawn: links that rely on it stop working')};

V2.actions['v2:report-open']=async el=>{
 const r=await api(`/api/v2/v2-reports/${num(el.dataset.id)}`);const signed=r.status==='signed';
 const links=signed?await api(`/api/v2/v2-reports/${num(r.id)}/links`):[];
 const shares=signed&&r.audience==='coach'&&selfLinksOn()?await api(`/api/v2/v2-reports/${num(r.id)}/share-requests`):null;
 const fields=r.fields.map(f=>f.kind!=='narrative'?`<div class="strip">${esc(f.label)} · ${f.included?'scores added automatically':badge('Not in this report')}</div>`
  :`${field(`${esc(f.label)} ${f.clinical_only?badge('Clinical-only: never shared','gold'):f.included?badge('Included'):badge('Not in this report')}`,`<textarea name="f:${esc(f.key)}" rows="3" maxlength="12000" ${signed?'disabled':''}>${esc(r.values[f.key]||'')}</textarea>`)}`).join('');
 openDialog(`${r.title} · ${AUD[r.audience]||r.audience}`,`<form data-form="v2:report-save" data-id="${num(r.id)}" data-revision="${num(r.revision)}" class="stack">${fields}${signed?'':`<div class="form-actions"><button class="btn" type="submit">Save draft</button>${button('Sign report','v2:report-sign','btn primary',`data-id="${num(r.id)}" data-revision="${num(r.revision)}"`)}</div>`}</form>`+
  shareBlock(r,shares)+(signed?`<div class="inline-actions"><a class="btn" href="/api/v2/v2-reports/${num(r.id)}/pdf">Download PDF</a>${state.v2flags?.SPPS_REPORT_LINKS_ENABLED?button('Create share link','v2:report-link','btn primary',`data-id="${num(r.id)}"`):''}</div><h3>Links</h3>${links.map(l=>`<div class="strip">${esc(l.recipient_label||'Link')} · expires ${fmtDate(l.expires_at)} · ${l.revoked_at?badge('Revoked'):button('Revoke','v2:link-revoke','btn small',`data-id="${num(l.id)}" data-report="${num(r.id)}"`)} · ${num(l.views.length)} view(s)${l.views.length?': '+l.views.map(v=>esc(v.outcome)).join(', '):''}</div>`).join('')||'<p class="small muted">No links.</p>'}`:''),
  signed?'Signed reports cannot be changed.':'Type the narrative yourself; scores and charts are added from the athlete\'s assessments. Sign the report before it can be shared.');
};
function reportValues(form){const v={};for(const el of form.elements){if(el.name&&el.name.startsWith('f:'))v[el.name.slice(2)]=el.value}return v}
V2.forms['v2:report-save']=async(form)=>{await api(`/api/v2/v2-reports/${num(form.dataset.id)}`,{method:'PUT',body:{values:reportValues(form),expected_revision:num(form.dataset.revision)}});await V2.actions['v2:report-open']({dataset:{id:form.dataset.id}});notify('Draft saved')};
V2.actions['v2:report-sign']=async el=>{const form=el.closest('form');let rev=num(el.dataset.revision);const saved=await api(`/api/v2/v2-reports/${num(el.dataset.id)}`,{method:'PUT',body:{values:reportValues(form),expected_revision:rev}});rev=saved.revision;await api(`/api/v2/v2-reports/${num(el.dataset.id)}/sign`,{method:'POST',body:{expected_revision:rev}});await V2.actions['v2:report-open'](el);notify('Report signed')};
V2.actions['v2:report-link']=async el=>{const l=await api(`/api/v2/v2-reports/${num(el.dataset.id)}/links`,{method:'POST',body:{expires_in_days:14}});openDialog('Share link',`<p>Copy this link now. It is shown only once and expires ${fmtDate(l.expires_at)}.</p><div class="field"><input readonly value="${esc(l.url)}" onclick="this.select()"></div>`,esc(l.notice))};
V2.actions['v2:link-revoke']=async el=>{await api(`/api/v2/report-links/${num(el.dataset.id)}/revoke`,{method:'POST',body:{}});await V2.actions['v2:report-open']({dataset:{id:el.dataset.report}});notify('Link revoked')};

V2.extraSettingsCards.push(()=>`<section class="card" data-v2="report-templates"><h3>Report templates</h3><p class="small muted">One line per field: key | label | narrative, scores or chart | audiences (athlete, guardian, coach, medical; leave empty for clinical-only) | instrument short name for scores/charts.</p>${button('New report template','v2:report-template','btn')}</section>`);
V2.actions['v2:report-template']=async()=>{openDialog('New report template',`<form data-form="v2:report-template" class="stack">${field('Name',`<input name="name" required minlength="2" maxlength="120">`)}${field('Fields',`<textarea name="fields" rows="6" required placeholder="summary | Summary | narrative | athlete, coach, medical&#10;formulation | Formulation notes | narrative |&#10;confidence | Confidence trend | chart | coach | CSAI-2"></textarea>`)}<div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Create template</button></div></form>`)};
V2.forms['v2:report-template']=async(form,raw)=>{
 const instruments=await api('/api/v2/instruments');
 const fields=raw('fields').split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const [key,label,kind,aud,inst]=l.split('|').map(x=>(x||'').trim());const f={key,label:label||key,kind:kind||'narrative',audiences:(aud||'').split(',').map(x=>x.trim()).filter(Boolean)};if(f.kind!=='narrative'){const m=instruments.find(i=>i.short_name===inst||i.name===inst);if(!m)throw Error(`Instrument "${inst}" not found`);f.instrument_id=m.id}return f});
 await api('/api/v2/report-templates',{method:'POST',body:{name:raw('name'),fields}});closeDialog();notify('Template created');
};

// ================================================================ Phase 6: practitioner-invited athlete loop
const loopOn=()=>flag('SPPS_ATHLETE_LOOP_ENABLED')&&flag('SPPS_PORTAL_ENABLED');
V2.extraAthleteButtons.push(a=>loopOn()?button('Athlete app','v2:loop','btn small',`data-id="${num(a.id)}"`):'');
V2.actions['v2:loop']=async el=>{
 const aid=num(el.dataset.id);const links=(await api('/api/v2/loop/links')).filter(l=>l.athlete_record_id===aid);
 const l=links.find(x=>x.status!=='ended');
 if(!l){openDialog('Invite to the athlete app',`<form data-form="v2:loop-invite" data-athlete="${aid}" class="stack">${field('E-mail (the guardian\'s for an under-18 athlete; leave empty to use the profile)','<input name="email" type="email">')}<button class="btn primary" type="submit">Send invitation</button></form>`,'The athlete (or, for under-18s, the guardian first) sees exactly what you will see before agreeing.');return}
 const act=l.status==='active'?await api(`/api/v2/loop/links/${num(l.id)}/activity`):null;
 const items=['stress','confidence','anxiety','mood','motivation'];
 openDialog('Athlete app · '+l.athlete_label,`<p>${badge(l.status)} ${l.open_alerts?badge(num(l.open_alerts)+' alert(s)','gold'):''}</p>`+
  (act?`<h3>Alerts</h3>${act.alerts.map(x=>`<div class="strip">${x.urgent?badge('URGENT','gold'):''} ${esc(x.reason)} · ${fmtDate(x.created_at)} ${x.acknowledged_at?'':button('Acknowledge','v2:loop-ack','btn small',`data-id="${num(x.id)}" data-athlete="${aid}"`)}</div>`).join('')||'<p class="small muted">None.</p>'}`+
   `<h3>Check-ins</h3>${act.checkins.map(c=>`<div class="strip">${fmtDate(c.due_on)} · ${c.status==='missing'?badge('missing'):Object.entries(c.answers||{}).map(([k,v])=>esc(k)+' '+num(v)).join(', ')}${c.urgent?' '+badge('urgent','gold'):''}</div>`).join('')||'<p class="small muted">No check-ins set.</p>'}`+
   `<form data-form="v2:loop-plan" data-id="${num(l.id)}" class="inline-actions"><select name="frequency"><option value="weekly">Weekly</option><option value="daily">Daily</option></select>${items.map(i=>`<label class="checkline"><input type="checkbox" name="i:${i}" ${act.plan&&act.plan.items.includes(i)?'checked':''}><span>${esc(i)}</span></label>`).join('')}<button class="btn small" type="submit">Set check-in (3–5 items)</button></form>`+
   `<form data-form="v2:loop-rule" data-id="${num(l.id)}" class="inline-actions"><select name="item">${items.map(i=>`<option>${i}</option>`).join('')}</select><select name="op"><option>&gt;=</option><option>&lt;=</option></select><input name="threshold" type="number" min="0" max="10" required style="width:5em"><button class="btn small" type="submit">Add alert rule</button></form>`+
   `<h3>Assignments</h3>${act.assignments.map(x=>`<div class="strip"><b>${esc(x.title)}</b> · ${esc(x.status)}</div>`).join('')||'<p class="small muted">None.</p>'}`+
   `<form data-form="v2:loop-assign" data-id="${num(l.id)}" class="stack">${field('Programme title','<input name="title" required minlength="2">')}${field('Tasks, one per line','<textarea name="tasks" rows="3" required></textarea>')}<button class="btn small" type="submit">Assign programme</button></form>`+
   `<form data-form="v2:loop-summary" data-id="${num(l.id)}" class="stack">${field('Release a summary (visible to the athlete and guardian)','<input name="title" required minlength="2" placeholder="Title"><textarea name="body" rows="2" required></textarea>')}<button class="btn small" type="submit">Release</button></form>`:'')+
  `<div class="inline-actions">${button('End link','v2:loop-end','btn small danger',`data-id="${num(l.id)}" data-athlete="${aid}"`)}</div>`,'Not a crisis service: urgent answers raise an urgent alert and an e-mail to you.');
};
V2.forms['v2:loop-invite']=async(form,raw)=>{const r=await api(`/api/v2/loop/athletes/${num(form.dataset.athlete)}/invite`,{method:'POST',body:{email:raw('email')||null}});openDialog('Invitation sent',`<p>Sent to the ${esc(r.kind)} by e-mail. It works in any browser or the app and expires in ${num(r.expires_in_days)} days.</p>`)};
V2.forms['v2:loop-plan']=async form=>{const items=[...form.elements].filter(e=>e.name?.startsWith('i:')&&e.checked).map(e=>e.name.slice(2));await api(`/api/v2/loop/links/${num(form.dataset.id)}/checkin-plan`,{method:'PUT',body:{frequency:form.elements.frequency.value,items}});notify('Check-in set')};
V2.forms['v2:loop-rule']=async(form,raw)=>{await api(`/api/v2/loop/links/${num(form.dataset.id)}/alert-rules`,{method:'POST',body:{item:raw('item'),op:raw('op'),threshold:Number(raw('threshold'))}});notify('Rule added')};
V2.forms['v2:loop-assign']=async(form,raw)=>{await api(`/api/v2/loop/links/${num(form.dataset.id)}/assignments`,{method:'POST',body:{kind:'programme',title:raw('title'),tasks:raw('tasks').split('\n')}});notify('Assigned')};
V2.forms['v2:loop-summary']=async(form,raw)=>{await api(`/api/v2/loop/links/${num(form.dataset.id)}/summaries`,{method:'POST',body:{title:raw('title'),body:raw('body')}});notify('Summary released')};
V2.actions['v2:loop-ack']=async el=>{await api(`/api/v2/loop/alerts/${num(el.dataset.id)}/acknowledge`,{method:'POST',body:{}});await V2.actions['v2:loop']({dataset:{id:el.dataset.athlete}})};
V2.actions['v2:loop-end']=async el=>{await api(`/api/v2/loop/links/${num(el.dataset.id)}/end`,{method:'POST',body:{}});closeDialog();notify('Link ended')};
V2.loaders.push(async()=>{if(state.user?.is_admin&&loopOn()){try{state.v2crisis=await api('/api/v2/admin/crisis-contacts')}catch{state.v2crisis=[]}}});
V2.extraAdminCards.push(()=>loopOn()?`<section class="card" data-v2="crisis"><h2>Crisis contacts</h2><p class="small muted">Shown on every athlete and guardian screen. One per line: name | phone | hours.</p><form data-form="v2:crisis" class="stack"><textarea name="contacts" rows="3">${esc((state.v2crisis||[]).map(c=>[c.name,c.phone,c.hours].join(' | ')).join('\n'))}</textarea><button class="btn small" type="submit">Save</button></form></section>`:'');
V2.forms['v2:crisis']=async(form,raw)=>{const contacts=raw('contacts').split('\n').map(l=>l.split('|').map(x=>x.trim())).filter(p=>p[0]).map(([name,phone,hours])=>({name,phone,hours:hours||''}));state.v2crisis=await api('/api/v2/admin/crisis-contacts',{method:'PUT',body:{contacts}});notify('Crisis contacts saved')};

// ================================================================ Phase 7: directory (practitioner + owner)
const dirOn=()=>flag('SPPS_DIRECTORY_ENABLED');
V2.loaders.push(async()=>{if(!dirOn())return;try{state.v2dir=await api('/api/v2/directory/profile')}catch{state.v2dir=null}
 if(state.user?.is_admin){try{state.v2dirQueue=await api('/api/v2/admin/directory')}catch{state.v2dirQueue=[]}}});
V2.extraSettingsCards.push(()=>{if(!dirOn()||!state.v2dir)return '';const p=state.v2dir;const v=k=>esc(p[k]??'');
 return `<section class="card" data-v2="directory"><h3>Public directory profile ${badge(p.status==='none'?'not started':p.status)}${p.listed?' '+badge('Listed','good'):''}</h3>${p.owner_note?`<p class="small">Owner note: ${esc(p.owner_note)}</p>`:''}<form data-form="v2:dir-profile" class="stack"><div class="field-grid">${field('Name shown','<input name="display_name" required minlength="3" value="'+v('display_name')+'">')}${field('City','<input name="city" value="'+v('city')+'">')}${field('Sports (comma-separated)','<input name="sports" value="'+v('sports')+'">')}${field('Athlete levels','<input name="athlete_levels" value="'+v('athlete_levels')+'">')}${field('Age groups','<input name="age_groups" value="'+v('age_groups')+'">')}${field('Languages','<input name="languages" value="'+v('languages')+'">')}${field('Online / in person',sel('delivery',['online','in_person','both'],p.delivery||'online'))}${field('Taking new clients',sel('taking',['yes','no'],p.taking_new_clients===false?'no':'yes'))}</div>${field('Qualifications','<textarea name="qualifications" rows="2" required minlength="4">'+v('qualifications')+'</textarea>')}${field('About','<textarea name="about" rows="3">'+v('about')+'</textarea>')}<div class="form-actions"><button class="btn" type="submit">Save</button>${button('Submit for publication','v2:dir-submit','btn primary')}</div></form><p class="small muted">Saving takes a published profile offline until the owner publishes the new text.</p></section>`});
V2.forms['v2:dir-profile']=async(form,raw)=>{state.v2dir=await api('/api/v2/directory/profile',{method:'PUT',body:{display_name:raw('display_name'),qualifications:raw('qualifications'),sports:raw('sports'),athlete_levels:raw('athlete_levels'),age_groups:raw('age_groups'),languages:raw('languages'),delivery:raw('delivery'),city:raw('city'),taking_new_clients:raw('taking')==='yes',about:raw('about')}});render();notify('Profile saved')};
V2.actions['v2:dir-submit']=async()=>{await api('/api/v2/directory/profile/submit',{method:'POST',body:{}});state.v2dir=await api('/api/v2/directory/profile');render();notify('Submitted to the owner')};
V2.extraAdminCards.push(()=>dirOn()?`<section class="card" data-v2="directory-admin"><h2>Directory</h2>${(state.v2dirQueue||[]).map(r=>`<div class="strip"><b>${esc(r.display_name)}</b> · ${esc(r.status)}${r.listed?' · listed until '+esc(r.verified_until):''}${r.reverification_due?' '+badge('Re-verify','gold'):''} <form data-form="v2:dir-publish" data-id="${num(r.practitioner_id)}" class="inline-actions">${['identity','qualification','registration'].map(k=>`<label class="checkline"><input type="checkbox" name="k:${k}"><span>${k}</span></label>`).join('')}<input name="checked_on" type="date" value="${new Date().toISOString().slice(0,10)}"><button class="btn small primary" type="submit">Publish</button></form>${r.listed?button('Delist','v2:dir-delist','btn small danger',`data-id="${num(r.practitioner_id)}"`):''}</div>`).join('')||'<p class="small muted">No profiles submitted.</p>'}</section>`:'');
V2.forms['v2:dir-publish']=async(form,raw)=>{const verifications=['identity','qualification','registration'].filter(k=>form.elements['k:'+k].checked).map(k=>({kind:k,checked_on:raw('checked_on')}));if(!verifications.length)throw Error('Tick what you checked');await api(`/api/v2/admin/directory/${num(form.dataset.id)}/publish`,{method:'POST',body:{verifications}});state.v2dirQueue=await api('/api/v2/admin/directory');render();notify('Published')};
V2.actions['v2:dir-delist']=async el=>{await api(`/api/v2/admin/directory/${num(el.dataset.id)}/delist`,{method:'POST',body:{reason:'Delisted by the platform owner'}});state.v2dirQueue=await api('/api/v2/admin/directory');render();notify('Delisted')};

/* ---------------------------------------------------------------- Phase 8: self-serve accounts (SPPS_SELF_SERVE_ENABLED) */
const selfOn=()=>flag('SPPS_SELF_SERVE_ENABLED')&&loopOn()&&dirOn();
const selfLinksOn=()=>selfOn()&&flag('SPPS_REPORT_LINKS_ENABLED');
function shareBlock(r,shares){
 if(!shares)return '';
 return `<h3>Coach link approved by the athlete</h3>${shares.map(s=>`<div class="strip">${esc(s.recipient_label||'Coach')} · ${badge(s.status,s.status==='approved'?'good':s.status==='pending'?'gold':'')}${s.expires_at?' · expires '+fmtDate(s.expires_at):''} · ${num(s.views.length)} view(s)${['pending','approved'].includes(s.status)?' '+button('Revoke','v2:share-revoke','btn small',`data-id="${num(s.id)}" data-report="${num(r.id)}"`):''}</div>`).join('')||'<p class="small muted">No requests yet.</p>'}<form data-form="v2:share-request" data-id="${num(r.id)}" class="inline-actions"><input name="recipient_label" maxlength="120" placeholder="Who it is for (e.g. club coach)"><select name="days"><option value="7">7 days</option><option value="14" selected>14 days</option><option value="30">30 days</option></select><button class="btn small primary" type="submit">Ask the athlete to approve</button></form><p class="small muted">The athlete (the guardian for under-18s) sees this signed report in the athlete app and decides. Nothing is sent unless they approve.</p>`;
}
V2.forms['v2:share-request']=async(form,raw)=>{await api(`/api/v2/v2-reports/${num(form.dataset.id)}/share-requests`,{method:'POST',body:{recipient_label:raw('recipient_label'),expires_in_days:num(raw('days'))}});await V2.actions['v2:report-open']({dataset:{id:form.dataset.id}});notify('Request sent to the athlete')};
V2.actions['v2:share-revoke']=async el=>{await api(`/api/v2/report-shares/${num(el.dataset.id)}/revoke`,{method:'POST',body:{}});await V2.actions['v2:report-open']({dataset:{id:el.dataset.report}});notify('Link revoked')};

/* ---------------------------------------------------------------- Booking-only directory, phase 2: my services */
V2.loaders.push(async()=>{try{{const d=await api('/api/v2/practitioner-services');state.v2services=d.services;state.v2listed=d}}catch{state.v2services=null}});
V2.extraSettingsCards.push(()=>{const list=state.v2services;if(!list)return '';const modeLabel=m=>m==='in_person'?'In person':'Online';
 const rows=list.map((s,i)=>`<div class="strip" data-v2-service="${num(s.id)}"><b>${esc(s.name)}</b> · ${num(s.duration_minutes)} min · ${modeLabel(s.mode)} ${s.active?badge('Active','good'):badge('Inactive')}${s.short_description?`<br><span class="small muted">${esc(s.short_description)}</span>`:''}<div class="inline-actions">${i>0?button('Move up','v2:service-move','btn small',`data-id="${num(s.id)}" data-dir="-1"`):''}${i<list.length-1?button('Move down','v2:service-move','btn small',`data-id="${num(s.id)}" data-dir="1"`):''}${button('Edit','v2:service-edit','btn small',`data-id="${num(s.id)}"`)}${s.active?button('Deactivate','v2:service-active','btn small',`data-id="${num(s.id)}" data-on="0"`):button('Activate','v2:service-active','btn small',`data-id="${num(s.id)}" data-on="1"`)}</div></div>`).join('');
 const L=state.v2listed||{},hint=L.listed?'':`<div class="notice" data-services-hidden>Athletes cannot see your services yet: your public profile is ${L.profile_status==='submitted'?'waiting for the owner to publish it':L.profile_status==='draft'?'a draft - submit it for publishing':'not created - add and submit it'}.</div>`;
 return `<section class="card" data-v2="services"><h3>My services</h3>${hint}<p class="small muted">Active services appear on your public directory card while you are listed. Clients choose one when they request an appointment. There are no prices: appointments are free.</p>${rows||'<p class="small muted">No services yet.</p>'}${serviceForm('v2:service-add',{duration_minutes:50,mode:'online'},'Add service')}</section>`});
function serviceForm(type,s,label,id){return `<form data-form="${type}" ${id?`data-id="${num(id)}"`:''} class="stack"><div class="field-grid">${field('Service name',`<input name="name" required minlength="3" maxlength="80" value="${esc(s.name||'')}">`)}${field('Minutes',`<input name="duration_minutes" type="number" min="20" max="120" required value="${num(s.duration_minutes)||50}">`)}${field('Mode',`<select name="mode"><option value="online" ${s.mode==='in_person'?'':'selected'}>Online</option><option value="in_person" ${s.mode==='in_person'?'selected':''}>In person</option></select>`)}</div>${field('Short description',`<textarea name="short_description" rows="2" maxlength="280">${esc(s.short_description||'')}</textarea>`)}<button class="btn" type="submit">${label}</button></form>`}
const serviceBody=raw=>({name:raw('name'),short_description:raw('short_description'),duration_minutes:num(raw('duration_minutes')),mode:raw('mode')});
const reloadServices=async()=>{const d=await api('/api/v2/practitioner-services');state.v2services=d.services;state.v2listed=d;render()};
V2.forms['v2:service-add']=async(form,raw)=>{await api('/api/v2/practitioner-services',{method:'POST',body:serviceBody(raw)});await reloadServices();notify('Service added')};
V2.forms['v2:service-save']=async(form,raw)=>{await api(`/api/v2/practitioner-services/${num(form.dataset.id)}`,{method:'PUT',body:serviceBody(raw)});closeDialog();await reloadServices();notify('Service saved')};
V2.actions['v2:service-edit']=async el=>{const s=(state.v2services||[]).find(x=>x.id===num(el.dataset.id));if(!s)return;openDialog('Edit service',serviceForm('v2:service-save',s,'Save',s.id),'Changes show on your directory card straight away while the service is active.')};
V2.actions['v2:service-active']=async el=>{await api(`/api/v2/practitioner-services/${num(el.dataset.id)}/${el.dataset.on==='1'?'activate':'deactivate'}`,{method:'POST',body:{}});await reloadServices();notify(el.dataset.on==='1'?'Service activated':'Service deactivated')};
V2.actions['v2:service-move']=async el=>{const ids=(state.v2services||[]).map(s=>s.id);const i=ids.indexOf(num(el.dataset.id)),j=i+num(el.dataset.dir);if(i<0||j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];state.v2services=(await api('/api/v2/practitioner-services/order',{method:'POST',body:{service_ids:ids}})).services;render()};

window.SPPSv2=V2;
})();
