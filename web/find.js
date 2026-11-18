/* SPPS v2 public directory (phase 7). Filters only, alphabetical, no ranking, no reviews. Contact is by appointment request from an SPPS account (no enquiry form). Existing classes only. */
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
 document.getElementById('results').innerHTML=d.results.map(r=>`<article class="practitioner-tile v1-practitioner card">${r.photo_url?`<img class="practitioner-photo" src="${esc(r.photo_url)}" alt="" width="96" height="96">`:''}<h3>${esc(r.display_name)}</h3><p class="small">${esc(r.qualifications)}</p><p class="small">${esc(r.sports.join(', '))} · ${esc(r.athlete_levels.join(', '))} · ${esc(r.age_groups.join(', '))}</p><p class="small">${esc(r.languages.join(', '))} · ${esc(r.delivery.replace('_',' '))}${r.city?' · '+esc(r.city):''} · ${r.taking_new_clients?'Taking new clients':'Not taking new clients'}</p><p>${r.badges.map(b=>`<span class="badge good">Verified ${esc(b.kind)} · ${esc(b.checked_on)}</span>`).join(' ')}</p><a class="btn small primary" href="/portal?practitioner=${esc(r.practitioner_id)}">Book appointment</a></article>`).join('')||'<div class="empty">No practitioners match these filters.</div>';
}
function boot(){
 root.innerHTML=`<div class="page-head"><div><div class="eyebrow">SPPS</div><h1>Find a Sports Psychologist</h1><p>Every listed practitioner's account is approved and the checks shown were made by the platform owner. Results are in alphabetical order.</p></div></div>
 <form data-f="filters" class="card field-grid">${f('Sport','<input name="sport">')}${f('Level','<input name="level" placeholder="club, national…">')}${f('Age group','<input name="age_group" placeholder="15-17…">')}${f('Language','<input name="language">')}${f('Online / in person','<select name="delivery"><option value="">Any</option><option value="online">Online</option><option value="in_person">In person</option></select>')}${f('City','<input name="city">')}<button class="btn primary" type="submit">Search</button></form>
 <div id="results" class="practitioner-grid"></div>`;
 api('/api/public/form-token').then(d=>formToken=d.form_token).catch(()=>{});
 search().catch(e=>notify(e.message));
}
document.addEventListener('submit',async e=>{const form=e.target.closest('form[data-f]');if(!form)return;e.preventDefault();try{
 if(form.dataset.f==='filters')return search();
}catch(err){notify(err.message)}});
boot();
})();
