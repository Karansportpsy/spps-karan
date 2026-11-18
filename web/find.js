/* SPPS v2 public directory (phase 7; enquiries from an SPPS account and switching practitioner, phase 8). Filters only, alphabetical, no ranking, no reviews. Existing classes only. */
(() => {
'use strict';
const root=document.getElementById('find'),noticeEl=document.getElementById('notice'),toast=document.getElementById('toast');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let formToken='',account=null;const switchFrom=new URLSearchParams(location.search).get('switch');const csrf=sessionStorage.getItem('spps.loop.csrf')||'';
function notify(m){toast.textContent=m;toast.classList.add('visible');setTimeout(()=>toast.classList.remove('visible'),6000)}
async function api(p,o={}){const r=await fetch(p,{credentials:'same-origin',cache:'no-store',...o,headers:{'Content-Type':'application/json',...(o.method&&o.method!=='GET'&&csrf?{'X-CSRF-Token':csrf}:{})}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Request failed');return d}
const f=(l,i)=>`<div class="field"><label>${l}${i}</label></div>`;
async function search(){
 const fd=new FormData(document.querySelector('form[data-f=filters]'));const q=new URLSearchParams();
 for(const [k,v] of fd.entries())if(v)q.set(k,v);
 const d=await api('/api/v2/public/directory?'+q);noticeEl.textContent=d.notice;
 document.getElementById('results').innerHTML=d.results.map(r=>`<article class="practitioner-tile v1-practitioner card">${r.photo_url?`<img class="practitioner-photo" src="${esc(r.photo_url)}" alt="" width="96" height="96">`:''}<h3>${esc(r.display_name)}</h3><p class="small">${esc(r.qualifications)}</p><p class="small">${esc(r.sports.join(', '))} · ${esc(r.athlete_levels.join(', '))} · ${esc(r.age_groups.join(', '))}</p><p class="small">${esc(r.languages.join(', '))} · ${esc(r.delivery.replace('_',' '))}${r.city?' · '+esc(r.city):''} · ${r.taking_new_clients?'Taking new clients':'Not taking new clients'}</p><p>${r.badges.map(b=>`<span class="badge good">Verified ${esc(b.kind)} · ${esc(b.checked_on)}</span>`).join(' ')}</p><button class="btn small primary" data-enquire="${r.practitioner_id}" data-name="${esc(r.display_name)}">Send an enquiry</button></article>`).join('')||'<div class="empty">No practitioners match these filters.</div>';
}
function boot(){
 root.innerHTML=`<div class="page-head"><div><div class="eyebrow">SPPS</div><h1>Find a Sports Psychologist</h1><p>Every listed practitioner's account is approved and the checks shown were made by the platform owner. Results are in alphabetical order.</p></div></div>
 <form data-f="filters" class="card field-grid">${f('Sport','<input name="sport">')}${f('Level','<input name="level" placeholder="club, national…">')}${f('Age group','<input name="age_group" placeholder="15-17…">')}${f('Language','<input name="language">')}${f('Online / in person','<select name="delivery"><option value="">Any</option><option value="online">Online</option><option value="in_person">In person</option></select>')}${f('City','<input name="city">')}<button class="btn primary" type="submit">Search</button></form>
 <div id="results" class="practitioner-grid"></div><div id="enquiry"></div>`;
 api('/api/public/form-token').then(d=>formToken=d.form_token).catch(()=>{});
 search().catch(e=>notify(e.message));
 // Signed in to an athlete or guardian account in this browser? Then enquiries go from the account (phase 8).
 if(csrf)api('/api/v2/portal/me').then(d=>api('/api/v2/portal/enquiries').then(()=>{account=d.data;if(switchFrom)notify('Choose the practitioner you want to move to. Your current link ends when the enquiry is sent.')})).catch(()=>{account=null});
}
document.addEventListener('submit',async e=>{const form=e.target.closest('form[data-f]');if(!form)return;e.preventDefault();try{
 if(form.dataset.f==='filters')return search();
 if(form.dataset.f==='account-enquiry'){const v=n=>form.elements[n].value;const body=JSON.stringify({practitioner_id:Number(form.dataset.id),sport:v('sport'),message:v('message'),form_token:formToken,website:v('website')});
  const r=switchFrom?await api(`/api/v2/portal/links/${encodeURIComponent(switchFrom)}/switch`,{method:'POST',body}):await api('/api/v2/portal/enquiries',{method:'POST',body});
  const msg=switchFrom?r.data.enquiry.message:r.data.message;document.getElementById('enquiry').innerHTML=`<div class="card"><p>${esc(msg)}</p><p><a class="btn small" href="/athlete">Back to my account</a></p></div>`;return}
 if(form.dataset.f==='enquiry'){const v=n=>form.elements[n].value;const r=await api(`/api/v2/public/directory/${form.dataset.id}/enquiries`,{method:'POST',body:JSON.stringify({name:v('name'),contact:v('contact'),role:v('role'),sport:v('sport'),message:v('message'),form_token:formToken,website:v('website')})});document.getElementById('enquiry').innerHTML=`<div class="card"><p>${esc(r.message)}</p></div>`}
}catch(err){notify(err.message)}});
document.addEventListener('click',e=>{const b=e.target.closest('[data-enquire]');if(!b)return;
 if(account){document.getElementById('enquiry').innerHTML=`<form data-f="account-enquiry" data-id="${esc(b.dataset.enquire)}" class="card stack"><h2>${switchFrom?'Move to':'Enquiry to'} ${esc(b.dataset.name)}</h2><p class="small">Sending as ${esc(account.display_name)} (${esc(account.email)}). Only this enquiry is shared; nothing else until you accept an invitation.${switchFrom?' Your current link ends when this is sent; your current practitioner keeps what you shared until now.':''}</p>${f('Sport','<input name="sport" required minlength="2" maxlength="80">')}${f('Short message (no health details, please)','<textarea name="message" required minlength="2" maxlength="600" rows="3"></textarea>')}<input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px"><p class="small muted">SPPS is not a crisis or emergency service.</p><button class="btn primary" type="submit">Send</button></form>`;document.getElementById('enquiry').scrollIntoView({behavior:'smooth'});return}
 document.getElementById('enquiry').innerHTML=`<form data-f="enquiry" data-id="${esc(b.dataset.enquire)}" class="card stack"><h2>Enquiry to ${esc(b.dataset.name)}</h2>${f('Your name','<input name="name" required minlength="2" maxlength="120">')}${f('E-mail or phone','<input name="contact" required minlength="5" maxlength="250">')}${f('I am','<select name="role"><option value="athlete">an athlete</option><option value="parent">a parent</option><option value="coach">a coach</option></select>')}${f('Sport','<input name="sport" required minlength="2" maxlength="80">')}${f('Short message (no health details, please)','<textarea name="message" required maxlength="600" rows="3"></textarea>')}<input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px"><p class="small muted">SPPS is not a crisis or emergency service.</p><button class="btn primary" type="submit">Send</button></form>`;
 document.getElementById('enquiry').scrollIntoView({behavior:'smooth'})});
boot();
})();
