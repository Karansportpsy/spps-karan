/* SPPS v2 public directory (phase 7). Filters only, alphabetical, no ranking, no reviews. Contact is by appointment request from an SPPS account (no enquiry form). Existing classes only. */
(() => {
'use strict';
const root=document.getElementById('find'),noticeEl=document.getElementById('notice'),toast=document.getElementById('toast');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let formToken='',account=null,results=[],book=null;const switchFrom=new URLSearchParams(location.search).get('switch');const csrf=sessionStorage.getItem('spps.loop.csrf')||'';
function notify(m){toast.textContent=m;toast.classList.add('visible');setTimeout(()=>toast.classList.remove('visible'),6000)}
async function api(p,o={}){const r=await fetch(p,{credentials:'same-origin',cache:'no-store',...o,headers:{'Content-Type':'application/json',...(o.method&&o.method!=='GET'&&csrf?{'X-CSRF-Token':csrf}:{})}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Request failed');return d}
const f=(l,i)=>`<div class="field"><label>${l}${i}</label></div>`;
async function search(){
 const fd=new FormData(document.querySelector('form[data-f=filters]'));const q=new URLSearchParams();
 for(const [k,v] of fd.entries())if(v)q.set(k,v);
 const d=await api('/api/v2/public/directory?'+q);noticeEl.textContent=d.notice;results=d.results;
 document.getElementById('results').innerHTML=d.results.map(r=>`<article class="practitioner-tile v1-practitioner card">${r.photo_url?`<img class="practitioner-photo" src="${esc(r.photo_url)}" alt="" width="96" height="96">`:''}<h3>${esc(r.display_name)}</h3><p class="small">${esc(r.qualifications)}</p><p class="small">${esc(r.sports.join(', '))} · ${esc(r.athlete_levels.join(', '))} · ${esc(r.age_groups.join(', '))}</p><p class="small">${esc(r.languages.join(', '))} · ${esc(r.delivery.replace('_',' '))}${r.city?' · '+esc(r.city):''} · ${r.taking_new_clients?'Taking new clients':'Not taking new clients'}</p><p>${r.badges.map(b=>`<span class="badge good">Verified ${esc(b.kind)} · ${esc(b.checked_on)}</span>`).join(' ')}</p>${(r.services||[]).length?`<ul class="small" data-services>${r.services.map(v=>`<li><b>${esc(v.name)}</b> · ${Number(v.duration_minutes)} min · ${v.mode==='in_person'?'In person':'Online'}${v.short_description?` — ${esc(v.short_description)}`:''}</li>`).join('')}</ul>`:''}<button class="btn small primary" type="button" data-act="book" data-pid="${esc(r.practitioner_id)}">Book appointment</button> <a class="small" href="/portal?practitioner=${esc(r.practitioner_id)}">Have an account? Sign in to book</a></article>`).join('')||'<div class="empty">No practitioners match these filters.</div>';
}

/* ---- guest booking: service > time > details > e-mail confirmation (no account needed) ---- */
const when=iso=>new Date(iso).toLocaleString([],{weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
function dialog(html){closeDialog();const d=document.createElement('div');d.className='dialog-backdrop';d.id='book-dialog';d.innerHTML=`<div class="dialog" role="dialog" aria-modal="true">${html}</div>`;document.body.appendChild(d);const f=d.querySelector('input,button');if(f)f.focus()}
function closeDialog(){document.getElementById('book-dialog')?.remove()}
function renderBook(){
 const r=book.r,svcs=r.services||[];
 const svc=book.service;let html=`<h2>Book with ${esc(r.display_name)}</h2><p class="small">Pick a service and a time, then give your name and e-mail. No account is needed to ask. The practitioner approves the request, and you create your account from the e-mail they send after that.</p>`;
 html+=`<h3>1. Service</h3>`+(svcs.length?svcs.map(v=>`<button type="button" class="list-btn${svc&&svc.service_id===v.service_id?' selected':''}" data-act="svc" data-id="${esc(v.service_id)}"><b>${esc(v.name)}</b><span>${Number(v.duration_minutes)} min · ${v.mode==='in_person'?'In person':'Online (video)'}${v.short_description?' — '+esc(v.short_description):''}</span></button>`).join(''):'<div class="empty">This practitioner has not listed any services yet.</div>');
 if(svc){html+=`<h3>2. Time</h3><p class="small muted">Times are shown in your device's time zone.</p>`;
  if(book.slots===null)html+='<p class="small">Loading open times…</p>';
  else if(!book.slots.length)html+='<div class="empty">No open times right now. Please check again later.</div>';
  else html+=`<div class="inline-actions" data-slots>${book.slots.slice(0,36).map(t=>`<button type="button" class="btn small${book.slot&&book.slot.slot_id===t.slot_id?' primary':''}" data-act="slot" data-id="${esc(t.slot_id)}">${esc(when(t.starts_at))}</button>`).join('')}</div>`}
 if(svc&&book.slot){html+=`<h3>3. Your details</h3><form data-f="guest"><div class="field-grid"><div class="field"><label>Your name</label><input name="name" required minlength="2" maxlength="120" autocomplete="name"></div><div class="field"><label>E-mail (we send the confirmation here)</label><input name="email" type="email" required maxlength="250" autocomplete="email"></div></div><div class="field"><label>Practical note for the practitioner (optional, no health details)</label><textarea name="note" maxlength="300"></textarea></div>
  <input name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px" value="">
  <label class="checkline"><input type="checkbox" name="adult" required> I am 18 or older. A parent or guardian books for a minor from their own account.</label>
  <label class="checkline"><input type="checkbox" name="consent" required> I accept the <a href="/terms" target="_blank" rel="noopener">Terms</a> and <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>, and I agree that SPPS shares my name, the service, the time and my note with ${esc(r.display_name)} to arrange this appointment${svc.mode==='online'?', including a live video consultation (never recorded)':''}.</label>
  <p class="small muted">SPPS is not an emergency service. If you are in crisis, contact local emergency services or a crisis line.</p>
  <div class="form-actions"><button type="button" class="btn" data-act="close">Cancel</button><button class="btn primary" type="submit">Request appointment</button></div></form>`}
 else html+=`<div class="form-actions"><button type="button" class="btn" data-act="close">Close</button></div>`;
 dialog(html);
}
async function loadSlots(){
 book.slots=null;book.slot=null;renderBook();
 try{const d=await api(`/api/v2/public/directory/${book.r.practitioner_id}/slots?service_id=${book.service.service_id}`);book.slots=d.slots}catch(e){book.slots=[];notify(e.message)}
 renderBook();
}
function submitted(){
 dialog(`<h2>Request submitted</h2><p>Check your mail for a confirmation. Open the link in the message and press <b>Confirm</b> to send your request to the practitioner.</p><p class="small muted">The link works for 24 hours. Nothing is sent to the practitioner until you confirm. If nothing arrives in a few minutes, check your spam folder.</p><div class="form-actions"><button type="button" class="btn primary" data-act="close">OK</button></div>`);
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-act]');if(!b)return;const a=b.dataset.act;
 try{
  if(a==='book'){const r=results.find(x=>String(x.practitioner_id)===b.dataset.pid);if(!r)return;book={r,service:null,slots:null,slot:null};if((r.services||[]).length===1){book.service=r.services[0];return loadSlots()}renderBook()}
  else if(a==='svc'){book.service=(book.r.services||[]).find(v=>String(v.service_id)===b.dataset.id);return loadSlots()}
  else if(a==='slot'){book.slot=book.slots.find(t=>String(t.slot_id)===b.dataset.id);renderBook()}
  else if(a==='close'){closeDialog();book=null}
  else if(a==='confirm'){b.disabled=true;const d=await api('/api/v2/public/guest-bookings/confirm',{method:'POST',body:JSON.stringify({token:b.dataset.token})});history.replaceState(null,'','/find');document.getElementById('confirm-card').innerHTML=`<h2>Request sent</h2><p>${esc(d.message)}</p><p class="small muted">${esc(d.notice||'')}</p>`}
 }catch(err){notify(err.message);if(b.disabled)b.disabled=false}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.getElementById('book-dialog')){closeDialog();book=null}});
async function sendGuest(form){
 const fd=new FormData(form),btn=form.querySelector('button[type=submit]');btn.disabled=true;
 try{
  if(!formToken){try{formToken=(await api('/api/public/form-token')).form_token}catch{}}
  await api('/api/v2/public/guest-bookings',{method:'POST',body:JSON.stringify({practitioner_id:book.r.practitioner_id,service_id:book.service.service_id,slot_id:book.slot.slot_id,name:fd.get('name'),email:fd.get('email'),note:fd.get('note')||'',adult_attested:!!fd.get('adult'),consent_accepted:!!fd.get('consent'),form_token:formToken,website:fd.get('website')||''})});
  book=null;submitted();
 }catch(err){notify(err.message);btn.disabled=false}
}
function confirmCard(token){return `<div class="card" id="confirm-card"><h2>Confirm your appointment request</h2><p>Press the button to send your request to the practitioner. Nothing is sent until you do.</p><button class="btn primary" type="button" data-act="confirm" data-token="${esc(token)}">Confirm my request</button></div>`}
function boot(){
 root.innerHTML=`<div class="page-head"><div><div class="eyebrow">SPPS</div><h1>Find a Sports Psychologist</h1><p>Every listed practitioner's account is approved and the checks shown were made by the platform owner. Results are in alphabetical order.</p></div></div>
 <form data-f="filters" class="card field-grid">${f('Sport','<input name="sport">')}${f('Level','<input name="level" placeholder="club, national…">')}${f('Age group','<input name="age_group" placeholder="15-17…">')}${f('Language','<input name="language">')}${f('Online / in person','<select name="delivery"><option value="">Any</option><option value="online">Online</option><option value="in_person">In person</option></select>')}${f('City','<input name="city">')}<button class="btn primary" type="submit">Search</button></form>
 <div id="results" class="practitioner-grid"></div>`;
 const ct=new URLSearchParams(location.search).get('confirm');if(ct)root.insertAdjacentHTML('afterbegin',confirmCard(ct));
 api('/api/public/form-token').then(d=>formToken=d.form_token).catch(()=>{});
 search().catch(e=>notify(e.message));
}
document.addEventListener('submit',async e=>{const form=e.target.closest('form[data-f]');if(!form)return;e.preventDefault();try{
 if(form.dataset.f==='filters')return search();
 if(form.dataset.f==='guest')return sendGuest(form);
}catch(err){notify(err.message)}});
boot();
})();
