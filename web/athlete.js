/* SPPS v2 athlete & guardian screens (phase 6; self-serve accounts phase 8). Existing classes only. Every screen shows the not-a-crisis notice
   and the owner's crisis contacts. All state is on the server, so links from e-mail work in any browser. */
(() => {
'use strict';
const root=document.getElementById('athlete'),crisisEl=document.getElementById('crisis'),toast=document.getElementById('toast');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const q=new URLSearchParams(location.search);let csrf=sessionStorage.getItem('spps.loop.csrf')||'';
// Invitation and verification tokens leave the address bar at once (history, screenshots, shared screens) and are
// kept only for this tab until used.
for(const k of ['invite','verify']){if(q.get(k))sessionStorage.setItem('spps.loop.'+k,q.get(k));else if(sessionStorage.getItem('spps.loop.'+k))q.set(k,sessionStorage.getItem('spps.loop.'+k))}
if(location.search)history.replaceState(null,'','/athlete');
const forget=k=>{q.delete(k);sessionStorage.removeItem('spps.loop.'+k)};
function notify(m){toast.textContent=m;toast.classList.add('visible');setTimeout(()=>toast.classList.remove('visible'),5000)}
async function api(path,opt={}){const o={credentials:'same-origin',cache:'no-store',...opt,headers:{...(opt.headers||{})}};
 if(opt.body){o.body=JSON.stringify(opt.body);o.headers['Content-Type']='application/json'}
 if(o.method&&o.method!=='GET'&&csrf)o.headers['X-CSRF-Token']=csrf;
 const r=await fetch(path,o);let d={};try{d=await r.json()}catch{}
 if(d&&d.notice)showCrisis(d);
 if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Request failed');return d}
function showCrisis(d){crisisEl.innerHTML=`<span><b>${esc(d.notice)}</b> ${(d.crisis_contacts||[]).map(c=>`${esc(c.name)}: ${esc(c.phone)}${c.hours?' ('+esc(c.hours)+')':''}`).join(' · ')}</span>`}
const card=(t,b)=>`<section class="card"><h2>${esc(t)}</h2>${b}</section>`;
const field=(l,i)=>`<div class="field"><label>${l}${i}</label></div>`;
async function boot(){
 try{showCrisis(await api('/api/v2/portal/crisis-contacts'))}catch{}
 if(q.get('verify')){const v=q.get('verify');forget('verify');try{await api('/api/v2/portal/auth/verify',{method:'POST',body:{token:v}});notify('E-mail confirmed. Sign in below.')}catch(e){notify(e.message)}}
 let me=null;try{me=(await api('/api/v2/portal/me')).data}catch{}
 if(!me)return authScreen();
 if(q.get('invite'))return inviteScreen(me,q.get('invite'));
 return homeScreen(me);
}
async function authScreen(){
 let preview='';if(q.get('invite')){try{const p=(await api(`/api/v2/portal/invites/${encodeURIComponent(q.get('invite'))}`)).data;preview=`<div class="login-notice"><strong>${esc(p.practitioner_name)} has invited you${p.kind==='guardian'?' as the guardian of '+esc(p.athlete_label):''}.</strong><p>Create an account with ${esc(p.email)} or sign in to continue.</p></div>`}catch(e){preview=`<div class="login-notice"><strong>${esc(e.message)}</strong></div>`}}
 root.innerHTML=preview+card('Sign in',`<form data-f="login" class="stack">${field('E-mail','<input name="email" type="email" required>')}${field('Password','<input name="password" type="password" required>')}<button class="btn primary" type="submit">Sign in</button></form>`)+
  card('Create an account',`<form data-f="register" class="stack">${field('Name','<input name="display_name" required minlength="2">')}${field('E-mail','<input name="email" type="email" required>')}${field('Password (10+ characters)','<input name="password" type="password" minlength="10" required>')}${field('I am','<select name="role"><option value="athlete">the athlete</option><option value="guardian">a parent or guardian</option></select>')}<label class="checkline"><input type="checkbox" name="terms" required><span class="check-copy">I accept the <a href="/terms" target="_blank">terms</a> and <a href="/privacy" target="_blank">Privacy Policy</a>.</span></label><button class="btn" type="submit">Create account</button></form>`)+
  (await selfServe()?card('Switched your account off?',`<form data-f="reactivate" class="stack">${field('E-mail','<input name="email" type="email" required>')}<button class="btn small" type="submit">Send a reactivation link</button></form>`):'');
}
let selfServeOn=null;
async function selfServe(){if(selfServeOn===null){try{await api('/api/v2/portal/crisis-contacts');const r=await fetch('/api/v2/portal/invitations',{credentials:'same-origin'});selfServeOn=r.status!==404}catch{selfServeOn=false}}return selfServeOn}
function scopeHtml(p){return `<ul>${p.scope.map(s=>`<li>${esc(s)}</li>`).join('')}</ul><p class="small muted">A guardian sees ${esc(p.guardian_sees)}. You can end the link at any time and keep a copy of your own data.</p>`}
function acceptFields(kind){return kind==='guardian'?field('Type your full name as your signature','<input name="evidence" required minlength="3">')+field("The athlete's e-mail address",'<input name="athlete_email" type="email" required>')+'<label class="checkline"><input type="checkbox" name="declaration" required><span class="check-copy">I am this athlete&#39;s parent or lawful guardian and I may give consent for them.</span></label>':''}
function snapshotHtml(c){if(!c)return '';return `<p class="small muted">${esc(c.audience_label||'')} · ${esc(c.title||'')}</p>`+(c.sections||[]).map(s=>`<div class="strip"><b>${esc(s.label)}</b>${s.kind==='narrative'?(s.text||'').split('\n').filter(x=>x.trim()).map(x=>`<p>${esc(x)}</p>`).join(''):`<p class="small">Assessment scores: ${(s.data?.series||[]).map(x=>esc(x.label)+' ('+(x.points||[]).length+' dated)').join(', ')}</p>`}</div>`).join('')}
async function selfServeCards(me,links){
 if(!await selfServe())return '';
 const [inv,enq]=await Promise.all([api('/api/v2/portal/invitations').then(d=>d.data),api('/api/v2/portal/enquiries').then(d=>d.data)]);
 let shares=[];try{shares=(await api('/api/v2/portal/report-shares')).data}catch{}
 let html='';
 for(const i of inv)html+=card(`Invitation from ${i.practitioner_name}`,`<p>${i.kind==='guardian'?'As guardian of '+esc(i.athlete_label)+', you':'You'} can link with this practitioner. They will see:</p>${scopeHtml(i)}<form data-f="inv-accept" data-id="${i.id}" class="stack">${acceptFields(i.kind)}<label class="checkline"><input type="checkbox" name="consent" required><span class="check-copy">I agree to share the information listed above with this practitioner.</span></label><button class="btn primary" type="submit">I agree</button></form>`);
 for(const s of shares.filter(x=>x.status==='pending'))html+=card(`Share a report with ${s.recipient_label||'your coach'}?`,`<p><b>${esc(s.practitioner_name)}</b> asks to share “${esc(s.title)}” about ${esc(s.athlete_label)}. This is exactly what the coach would see:</p>${snapshotHtml(s.content)}<form data-f="share-approve" data-id="${s.id}" class="stack">${field("Coach's e-mail (optional: we send the link for you)",'<input name="recipient_email" type="email">')}<div class="inline-actions"><button class="btn primary" type="submit">Approve</button><button class="btn" type="button" data-a="share-decline" data-id="${s.id}">Decline</button></div></form>`);
 const approved=shares.filter(x=>x.status==='approved');
 if(approved.length)html+=card('Reports you have shared',approved.map(s=>`<div class="strip"><b>${esc(s.title)}</b> · ${esc(s.recipient_label||'coach')} · until ${esc(String(s.expires_at||'').slice(0,10))} <button class="btn small" data-a="share-withdraw" data-id="${s.id}">Withdraw</button></div>`).join(''));
 html+=card('Your enquiries',(enq.map(r=>`<div class="strip"><b>${esc(r.practitioner_name)}</b> · ${esc(r.sport)} · ${esc(r.status)}${r.status==='sent'?` <button class="btn small" data-a="enq-withdraw" data-id="${r.id}">Withdraw</button>`:''}</div>`).join('')||'<p class="small muted">None yet.</p>')+`<p><a class="btn small primary" href="/find">Find a sports psychologist</a></p><p class="small muted">Only your enquiry is sent. Nothing else is shared until you accept an invitation.</p>`);
 return html;
}
function accountCard(on){return card('Your account',`<a class="btn small" href="/api/v2/portal/export" download>Download my data</a> <button class="btn small" data-a="logout">Sign out</button>`+(on?`<form data-f="deactivate" class="stack">${field('Password','<input name="password" type="password" required>')}<button class="btn small" type="submit">Switch my account off</button></form><form data-f="delete" class="stack">${field('Password','<input name="password" type="password" required>')}${field('Type DELETE to confirm','<input name="confirm" required>')}<p class="small muted">Deleting removes your account and sign-in details and ends every practitioner link. Download your data first.</p><button class="btn small danger" type="submit">Delete my account</button></form>`:''))}
async function inviteScreen(me,token){
 const p=(await api(`/api/v2/portal/invites/${encodeURIComponent(token)}`)).data;
 root.innerHTML=card('What your practitioner will see',`<p><b>${esc(p.practitioner_name)}</b> invites ${p.kind==='guardian'?'you, as guardian of '+esc(p.athlete_label)+',':'you'} to SPPS.</p><ul>${p.scope.map(s=>`<li>${esc(s)}</li>`).join('')}</ul><p class="small muted">A guardian sees ${esc(p.guardian_sees)}. You can end the link at any time and keep a copy of your own data.</p><form data-f="accept" data-token="${esc(token)}" class="stack">${p.kind==='guardian'?field('Type your full name as your signature','<input name="evidence" required minlength="3">')+field("The athlete's e-mail address",'<input name="athlete_email" type="email" required>')+'<label class="checkline"><input type="checkbox" name="declaration" required><span class="check-copy">I am this athlete&#39;s parent or lawful guardian and I may give consent for them.</span></label>':''}<label class="checkline"><input type="checkbox" name="consent" required><span class="check-copy">I agree to share the information listed above with this practitioner.</span></label><button class="btn primary" type="submit">I agree</button></form>`);
}
async function homeScreen(me){
 const links=(await api('/api/v2/portal/links')).data;let html='';const on=await selfServe();const extra=await selfServeCards(me,links);
 for(const l of links){
  if(l.status!=='active'&&l.status!=='guardian_consented'){html+=card(l.practitioner_name,`<p>${esc(l.status)}</p>`);continue}
  if(l.you_are==='guardian'){const v=(await api(`/api/v2/portal/links/${l.id}/guardian-view`)).data;
   html+=card(`${l.athlete_label} · ${l.practitioner_name}`,`<div class="strip">${v.participation.assignments_completed} completed · ${v.participation.assignments_open} open · ${v.participation.checkins_submitted} check-ins</div>${v.summaries.map(s=>`<div class="strip"><b>${esc(s.title)}</b><p>${esc(s.body)}</p></div>`).join('')}<button class="btn small" data-a="end" data-id="${l.id}">End link</button>`);continue}
  const tasks=(await api(`/api/v2/portal/links/${l.id}/assignments`)).data,ch=(await api(`/api/v2/portal/links/${l.id}/checkins`)).data;
  const open=tasks.filter(t=>t.status==='assigned');
  html+=card(`With ${l.practitioner_name}`,
   (ch.items.length?`<h3>Check-in (${esc(ch.frequency)})</h3><form data-f="checkin" data-id="${l.id}" class="stack">${ch.items.map(i=>field(`${esc(i.label)} — ${esc(ch.scale)}`,`<input name="${esc(i.key)}" type="number" min="0" max="10" required>`)).join('')}<label class="checkline"><input type="checkbox" name="urgent"><span class="check-copy">${esc(ch.urgent_question)}</span></label><button class="btn primary" type="submit">Send check-in</button></form><p class="small muted">${ch.due.filter(d=>d.status==='missing').length} missed</p>`:'')+
   open.map(t=>t.kind==='programme'?`<div class="strip"><b>${esc(t.title)}</b> ${esc(t.schedule)}<ul>${t.content.tasks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><button class="btn small" data-a="done" data-id="${t.id}">Mark done</button></div>`
    :`<form data-f="instrument" data-id="${t.id}" class="card stack"><h3>${esc(t.title)}</h3>${t.content.items.map(i=>field(esc(i.text),`<input name="${i.id}" type="number" min="${t.content.response_min}" max="${t.content.response_max}" required>`)).join('')}<button class="btn primary" type="submit">Send answers</button></form>`).join('')+
   `<div class="inline-actions"><button class="btn small" data-a="guardian" data-id="${l.id}" data-on="${l.guardian_detail_allowed_by_you?0:1}">${l.guardian_detail_allowed_by_you?'Stop sharing answers with my guardian':'Let my guardian see my answers'}</button><button class="btn small" data-a="end" data-id="${l.id}">End link</button>${selfServeOn?`<a class="btn small" href="/find?switch=${l.id}">Switch practitioner</a>`:''}</div>`);
 }
 root.innerHTML=extra+(html||card('No practitioner yet',on?'<p>Find a practitioner and send an enquiry, or open the link from an invitation e-mail.</p>':'<p>When a practitioner invites you, open the link from the e-mail.</p>'))+accountCard(on);
}
document.addEventListener('submit',async e=>{const f=e.target.closest('form[data-f]');if(!f)return;e.preventDefault();const v=n=>f.elements[n]?.value;try{
 if(f.dataset.f==='login'){const r=await api('/api/v2/portal/auth/login',{method:'POST',body:{email:v('email'),password:v('password')}});csrf=r.csrf;sessionStorage.setItem('spps.loop.csrf',csrf);return boot()}
 if(f.dataset.f==='register'){await api('/api/v2/portal/auth/register',{method:'POST',body:{email:v('email'),password:v('password'),display_name:v('display_name'),role:v('role'),terms_accepted:f.elements.terms.checked}});notify('Check your e-mail to confirm your address');return}
 if(f.dataset.f==='accept'){await api(`/api/v2/portal/invites/${encodeURIComponent(f.dataset.token)}/accept`,{method:'POST',body:{consent:f.elements.consent.checked,guardian_evidence:v('evidence')||'',guardian_declaration:!!f.elements.declaration?.checked,athlete_email:v('athlete_email')||null}});forget('invite');notify('Linked');return boot()}
 if(f.dataset.f==='inv-accept'){await api(`/api/v2/portal/invitations/${f.dataset.id}/accept`,{method:'POST',body:{consent:f.elements.consent.checked,guardian_evidence:v('evidence')||'',guardian_declaration:!!f.elements.declaration?.checked,athlete_email:v('athlete_email')||null}});notify('Linked');return boot()}
 if(f.dataset.f==='share-approve'){const r=(await api(`/api/v2/portal/report-shares/${f.dataset.id}/approve`,{method:'POST',body:{recipient_email:v('recipient_email')||null}})).data;root.insertAdjacentHTML('afterbegin',card('Report link',`<p>${r.mailed?'Sent to the coach. ':''}Copy this link now; it is shown only once and works until ${esc(String(r.expires_at).slice(0,10))}.</p><div class="field"><input readonly value="${esc(r.url)}" onclick="this.select()"></div>`));return}
 if(f.dataset.f==='reactivate'){await api('/api/v2/portal/auth/reactivate',{method:'POST',body:{email:v('email')}});notify('If the account is switched off, a link is on its way');return}
 if(f.dataset.f==='deactivate'){if(!confirm('Switch your account off? You can reactivate it by e-mail.'))return;await api('/api/v2/portal/account/deactivate',{method:'POST',body:{password:v('password')}});sessionStorage.removeItem('spps.loop.csrf');csrf='';notify('Account switched off');return boot()}
 if(f.dataset.f==='delete'){if(!confirm('Delete your account for good?'))return;await api('/api/v2/portal/account/delete',{method:'POST',body:{password:v('password'),confirm:v('confirm')}});sessionStorage.removeItem('spps.loop.csrf');csrf='';notify('Account deleted');return boot()}
 if(f.dataset.f==='checkin'){const answers={};for(const el of f.elements)if(el.type==='number')answers[el.name]=Number(el.value);await api(`/api/v2/portal/links/${f.dataset.id}/checkins`,{method:'POST',body:{answers,urgent:f.elements.urgent.checked}});notify('Check-in sent');return boot()}
 if(f.dataset.f==='instrument'){const answers={};for(const el of f.elements)if(el.type==='number')answers[el.name]=Number(el.value);await api(`/api/v2/portal/assignments/${f.dataset.id}/submit`,{method:'POST',body:{answers}});notify('Answers sent');return boot()}
}catch(err){notify(err.message)}});
document.addEventListener('click',async e=>{const b=e.target.closest('[data-a]');if(!b)return;try{
 if(b.dataset.a==='end'){if(!confirm('End this link? Your practitioner will no longer see new information.'))return;await api(`/api/v2/portal/links/${b.dataset.id}/end`,{method:'POST',body:{}});return boot()}
 if(b.dataset.a==='guardian'){await api(`/api/v2/portal/links/${b.dataset.id}/guardian-detail`,{method:'POST',body:{enabled:b.dataset.on==='1'}});return boot()}
 if(b.dataset.a==='done'){await api(`/api/v2/portal/assignments/${b.dataset.id}/submit`,{method:'POST',body:{answers:{done:true}}});return boot()}
 if(b.dataset.a==='share-decline'){await api(`/api/v2/portal/report-shares/${b.dataset.id}/decline`,{method:'POST',body:{}});return boot()}
 if(b.dataset.a==='share-withdraw'){await api(`/api/v2/portal/report-shares/${b.dataset.id}/withdraw`,{method:'POST',body:{}});notify('Link withdrawn');return boot()}
 if(b.dataset.a==='enq-withdraw'){await api(`/api/v2/portal/enquiries/${b.dataset.id}/withdraw`,{method:'POST',body:{}});return boot()}
 if(b.dataset.a==='logout'){await api('/api/v2/portal/auth/logout',{method:'POST',body:{}});sessionStorage.removeItem('spps.loop.csrf');csrf='';return boot()}
}catch(err){notify(err.message)}});
boot();
})();
