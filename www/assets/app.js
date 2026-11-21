(() => {
'use strict';
const app=document.getElementById('app'), modal=document.getElementById('modal-root'),toastEl=document.getElementById('toast');
const state={supabaseAuth:false,supabaseEmail:'',pendingSignupEmail:'',loginNotice:null,passwordRecovery:false,adminReviews:[],athleteLoading:false,theme:localStorage.getItem('modelc-theme')||'original',mode:location.pathname==='/register'?'register':['/studio','/admin'].includes(location.pathname)?'studio':location.pathname==='/services'?'public':'directory',view:location.pathname==='/admin'?'admin':'overview',user:null,csrf:'',services:[],dashboard:null,athletes:[],athleteId:null,athleteDetail:null,caseId:null,caseDetail:null,factors:[],observations:[],interventions:[],reports:[],selectedGraph:null,practitioners:[],publicProgrammes:[],ownProgrammes:[],programmeEnquiries:[],organisationQuotes:[],bookings:[],appointments:[],appointmentTab:'Pending',slots:[],offlineAssessments:[],injuries:[],indices:{series:[]},chartType:'line',tierInfo:null,privateProfile:{},publicPlans:null,registrationToken:null,registrationPlan:null,registrationStatus:null,freeAccess:true};
document.body.dataset.theme=state.theme;
const esc=(v='')=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const short=s=>(s||'').slice(0,2).toUpperCase();
const when=s=>s?new Date(s).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}):'—';
const val=(form,name)=>form.elements.namedItem(name)?.value?.trim()||'';
const fdate=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
// Clear all confidential per-practitioner UI state before switching accounts.
// Public directory preferences stay in memory; private caseload data does not.
function clearPrivateState(){
 if(window.SPPSPush)SPPSPush.stop();
 sessionEpoch++;athleteLoadSequence++;athleteSelectionRevision++;workspaceLoadSequence++;
 Object.assign(state,{user:null,csrf:'',view:'overview',dashboard:null,athletes:[],athleteId:null,athleteDetail:null,caseId:null,caseDetail:null,factors:[],observations:[],interventions:[],reports:[],selectedGraph:null,bookings:[],appointments:[],appointmentTab:'Pending',slots:[],ownProgrammes:[],programmeEnquiries:[],organisationQuotes:[],offlineAssessments:[],injuries:[],indices:{series:[]},tierInfo:null,lifecycle:undefined,privateProfile:{},adminReviews:[],supabaseEmail:'',pendingSignupEmail:'',passwordRecovery:false,mfaFactorId:null,mfaEnrollment:false});
}
let sessionEpoch=0, integrationDevelopment=false, refreshing=false;
let toastId=0;
function notify(msg){toastEl.textContent=msg;toastEl.classList.add('visible');const id=++toastId;setTimeout(()=>{if(toastId===id)toastEl.classList.remove('visible')},5200)}
async function api(path,options={}){
 const requestEpoch=sessionEpoch;
 // Always fetch live API records. The Android WebView and desktop must not
 // reuse a previous GET response after the other device has made a write.
 const opts={credentials:'same-origin',cache:'no-store',...options,headers:{...(options.headers||{})}};
 if(options.body && typeof options.body==='object' && !(options.body instanceof FormData)) {opts.body=JSON.stringify(options.body);opts.headers['Content-Type']='application/json'}
 if(!['GET','HEAD'].includes((options.method||'GET').toUpperCase()) && state.csrf && !path.startsWith('/api/auth/login') && !path.startsWith('/api/public/')) opts.headers['X-CSRF-Token']=state.csrf;
 let r;try{r=await fetch(path,opts)}catch{throw Error('SPPS server could not be reached. Refresh records before retrying a save to avoid duplicates.')}
 let data={};try{data=await r.json()}catch{}
 if(requestEpoch!==sessionEpoch)throw Error('Session changed; stale response discarded');
 if(r.status===401 && state.user && path!=='/api/auth/login'){clearPrivateState();state.mode='studio';closeDialog();render();notify('Session expired. Sign in again to access private records.')}
 if(!r.ok){let msg=typeof data.detail==='string'?data.detail:(Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('; '):`Request failed`);
  // The hosted profile answers 503 for modules that are switched off in this release. That is not a fault.
  const unavailable=r.status===503&&/outside the isolated development milestone/.test(msg);
  const err=Error(unavailable?'This feature is not available in this release.':`${msg} (HTTP ${r.status})`);if(unavailable)err.unavailable=true;throw err}
 return data;
}
const badge=(text,type='')=>`<span class="badge ${type}">${esc(text)}</span>`;
const button=(label,action,cl='btn',extra='')=>`<button type="button" class="${cl}" data-action="${action}" ${extra}>${label}</button>`;
const themePick=()=>`<div class="theme-pick" aria-label="Theme preference" role="group">${['original','light','dark'].map(t=>`<button aria-pressed="${t===state.theme}" data-action="theme" data-theme-name="${t}" type="button">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</div>`;
function brand(){return `<a class="brand" href="/" data-action="directory"><img class="brand-v1" src="/assets/spps-v1-logo.png" alt="SPPS Sport Psychology Practitioner Suite" width="174" height="74"><span class="sr-only">SPPS 1.0 Home</span></a>`}

const SOCIAL_ICON_PATHS={
 Instagram:'<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r=".9" fill="currentColor" stroke="none"/>',
 Facebook:'<path d="M15 21v-8h3l.45-4H15V7.5c0-1.5.6-2.2 2.2-2.2H19V2.1A19 19 0 0 0 16.5 2C12.7 2 11 4.3 11 7.4V9H8v4h3v8"/>',
 LinkedIn:'<rect x="3" y="9" width="4" height="12" rx=".6"/><circle cx="5" cy="5" r="2"/><path d="M11 21V9h4v1.6c.8-1.2 1.9-1.8 3.5-1.8 2.3 0 3.5 1.6 3.5 4.3V21h-4v-6.3c0-1.5-.4-2.3-1.5-2.3S15 13.2 15 14.7V21"/>',
 YouTube:'<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3z"/>',
 Spotify:'<circle cx="12" cy="12" r="10"/><path d="M5.5 9c4.8-1.4 9.5-.9 13.2 1M6.5 12.6c3.7-1 7.7-.5 10.9 1M7.7 16c2.6-.6 5.6-.2 8.1.9"/>',
 Threads:'<path d="M17.8 9.2c-.6-3-2.6-4.9-5.8-4.9-4 0-6.6 2.8-6.6 7.8 0 4.9 2.5 7.8 6.6 7.8 3.5 0 5.9-2.1 5.9-5.1 0-2.3-1.5-3.8-3.9-3.8-2.3 0-3.6 1.1-3.6 2.7 0 1.3.9 2.1 2.2 2.1 2.4 0 3.8-2 3.3-4.4-.4-2-2.2-3.1-5.1-3.1"/>'
};
const socialIcon=name=>'<svg class="social-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+SOCIAL_ICON_PATHS[name]+'</svg>';
function socials(){
 const links=[['Instagram','https://www.instagram.com/win_mind_1/'],['Facebook','https://www.facebook.com/share/1FLf6LdARm/'],['LinkedIn','https://www.linkedin.com/in/dr-karanbir-singh-ph-d-82163b54/'],['YouTube',''],['Spotify','https://open.spotify.com/show/6UjTBFncbZ3tuyM3FfNfSc'],['Threads','https://www.threads.com/@win_mind_1']];
 return '<div class="socials-v1" aria-label="WinMindPerform social profiles">'+links.map(([name,url])=>{
  const icon=socialIcon(name);
  return url?'<a class="social-v1" href="'+url+'" target="_blank" rel="noopener noreferrer" title="'+name+'" aria-label="'+name+'">'+icon+'</a>':'<span class="social-v1 social-v1-inactive" title="'+name+' link not configured" aria-label="'+name+' link not configured">'+icon+'</span>';
 }).join('')+'</div>';
}

const OWNER_EMAIL='karansportpsy@gmail.com';
// A listed image that cannot load (for example the owner's hosted portrait) falls back to a local portrait.
document.addEventListener('error',e=>{const t=e.target;if(t&&t.tagName==='IMG'&&!t.dataset.fallback&&(/^https:\/\/www\.winmindperform\.com\//.test(t.src)||t.classList.contains('practitioner-photo'))){t.dataset.fallback='1';t.src='/assets/avatars/placeholder.svg'}},true);
let supabaseClient=null, supabaseSdkPromise=null;
async function initSupabaseAuth(){
 let cfg;
 try{cfg=await api('/api/public/supabase-auth-config')}catch{return}
 state.supabaseAuth=!!cfg?.enabled;
 if(!state.supabaseAuth)return;
 if(!window.supabase?.createClient){
  if(!supabaseSdkPromise)supabaseSdkPromise=new Promise((resolve,reject)=>{
   const script=document.createElement('script');
   script.src='https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.118.0/dist/umd/supabase.js';
   script.async=true;script.onload=resolve;script.onerror=()=>reject(Error('Secure sign-in library unavailable'));
   document.head.appendChild(script);
  });
  await supabaseSdkPromise;
 }
 if(!window.supabase?.createClient)throw Error('Supabase Auth library unavailable');
 supabaseClient=window.supabase.createClient(cfg.url,cfg.publishable_key,{
  auth:{autoRefreshToken:true,persistSession:true,detectSessionInUrl:true,
        flowType:'implicit',storage:window.sessionStorage}
 });
 supabaseClient.auth.onAuthStateChange((event)=>{
  if(event==='PASSWORD_RECOVERY'){state.passwordRecovery=true;render()}
 });
 const {data}=await supabaseClient.auth.getUser();
 state.supabaseEmail=data?.user?.email||'';
 state.emailVerifiedNotice=location.pathname==='/register'&&Boolean(data?.user?.email_confirmed_at);
 state.carriedApplication=state.emailVerifiedNotice?carriedApplication(data.user):null;
 render();
}
async function requireSupabase(){
 if(!state.supabaseAuth||!supabaseClient)throw Error('Supabase authentication is not configured on this installation');
 return supabaseClient;
}
// The application is kept in this browser between pressing "Create free account" and clicking the email link
// (the link opens a new tab, so it must be localStorage). It never holds the password, expires after a day,
// and is removed as soon as the application has been submitted.
const APPLICATION_DRAFT_KEY='spps.application-draft';
const APPLICATION_DRAFT_TTL_MS=24*60*60*1000;
function saveApplicationDraft(draft){
 // Returns false when the browser refuses storage (private mode); the sign-up dialog then says the form must be re-entered.
 try{localStorage.setItem(APPLICATION_DRAFT_KEY,JSON.stringify({...draft,saved_at:Date.now()}));return true}catch{return false}
}
function clearApplicationDraft(){try{localStorage.removeItem(APPLICATION_DRAFT_KEY)}catch{}}
function loadApplicationDraft(email){
 try{
  const draft=JSON.parse(localStorage.getItem(APPLICATION_DRAFT_KEY)||'null');
  if(!draft||typeof draft!=='object')return null;
  if(!(Date.now()-Number(draft.saved_at)<=APPLICATION_DRAFT_TTL_MS)){clearApplicationDraft();return null}
  return String(draft.email||'').toLowerCase()===String(email||'').toLowerCase()?draft:null;
 }catch{return null}
}
const APPLICATION_SENT_NOTICE={title:'Your request has been sent for approval',
  text:'Your email is confirmed. The platform administrator will review your profile. You will receive an email when it is approved; then sign in here with the email and password you registered with.'};
// The application text also travels with the Supabase account (user_metadata) so that confirming the email in a
// different browser than the one that filled the form still finds it. That is the normal case on Android: the app
// is its own WebView and the mail app opens links in Chrome. Text only (the photo is picked again), and kept small
// because user_metadata is part of every access token and the server rejects tokens over 8192 bytes.
const APPLICATION_META_KEY='application';
const APPLICATION_META_MAX_BYTES=4000;
function applicationForAccount(draft){
 const app={name:String(draft.name||'').trim(),qualifications:String(draft.qualifications||''),
  experience_years:Number(draft.experience_years)||0,achievements:String(draft.achievements||''),specialisms:String(draft.specialisms||'')};
 return new TextEncoder().encode(JSON.stringify(app)).length<=APPLICATION_META_MAX_BYTES?app:null;
}
// Read back as untrusted text: clipped to the form's own limits and only ever placed into the form escaped.
function carriedApplication(user){
 const a=user?.user_metadata?.[APPLICATION_META_KEY];
 if(!a||typeof a!=='object')return null;
 return {name:String(a.name||'').slice(0,120),qualifications:String(a.qualifications||'').slice(0,1000),
  experience_years:Math.min(70,Math.max(0,Number(a.experience_years)||0)),achievements:String(a.achievements||'').slice(0,1500),
  specialisms:String(a.specialisms||'').slice(0,250)};
}
// The application is in: drop the carried copy from the account, end the email session and set up the sign-in page message.
async function finishApplication(sb){
 try{await sb.auth.updateUser({data:{[APPLICATION_META_KEY]:null}})}catch{/* a leftover copy is small and harmless */}
 clearApplicationDraft();
 await sb.auth.signOut({scope:'local'});
 state.supabaseEmail='';state.emailVerifiedNotice=false;state.carriedApplication=null;
 state.loginNotice={...APPLICATION_SENT_NOTICE};
}
// Runs after the email link is clicked: sends the saved application, then shows the practitioner sign-in page
// with a message that the request is waiting for approval.
async function submitSavedApplication(draft){
 const sb=await requireSupabase();
 const {data:sessionData}=await sb.auth.getSession();
 const token=sessionData?.session?.access_token;
 if(!token)return false;
 try{
  await api('/api/public/practitioner-registration',{method:'POST',body:{
   name:draft.name,email:draft.email,password:null,supabase_access_token:token,
   qualifications:draft.qualifications,experience_years:Number(draft.experience_years),
   achievements:draft.achievements,specialisms:draft.specialisms,
   consultation_fee_paise:0,plan:'free',photo_base64:draft.photo_base64,terms_accepted:true
  }});
 }catch(err){
  // Already registered counts as submitted; any other failure keeps the draft so the person can retry.
  if(!/already registered/i.test(err.message))throw err;
 }
 await finishApplication(sb);
 state.mode='studio';navigateSection('studio','/studio');
 return true;
}
const LINK_SESSION_MESSAGE='Your email is confirmed. Enter your password and press Sign in to continue.';
// A session created by clicking the confirmation link does not prove the password; the server only accepts
// sessions whose authentication methods include "password" (server/supabase_practitioner_auth.py).
function sessionUsedPassword(session){
 try{
  const claims=JSON.parse(atob(session.access_token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
  return Array.isArray(claims.amr)&&claims.amr.some(m=>m&&m.method==='password');
 }catch{return false}
}
async function handleSupabaseLogin(){
 const sb=await requireSupabase();
 const {data:sessionData,error:sessionError}=await sb.auth.getSession();
 if(sessionError||!sessionData?.session?.access_token)throw Error('Email/password session unavailable. Sign in again.');
 const {data:userData,error:userError}=await sb.auth.getUser();
 if(userError||!userData?.user?.email_confirmed_at)throw Error('Verify your email before signing in.');
 if(!sessionUsedPassword(sessionData.session))throw Error(LINK_SESSION_MESSAGE);
 let result;
 try{
  result=await api('/api/auth/supabase-exchange',{method:'POST',body:{
   access_token:sessionData.session.access_token,terms_accepted:true
  }});
 }catch(err){await showApplicationStatus(sessionData.session.access_token,err,userData.user.email,carriedApplication(userData.user));return}
 clearPrivateState();state.user=result;state.csrf=result.csrf;state.loginNotice=null;if(window.SPPSPush)SPPSPush.start({scope:'workspace',csrf:state.csrf});
 await loadAll();closeDialog();render();notify('Signed in with your verified email account');
}
// A confirmed account that never sent an application (for example the email link was opened in another browser)
// is taken to the application form. Its password session stays open, so the form can send the application
// without a second email. The owner account is never sent here.
function routeToApplicationForm(email,carried){
 state.supabaseEmail=email||state.supabaseEmail;state.emailVerifiedNotice=true;state.carriedApplication=carried||null;
 navigateSection('register','/register');render();window.scrollTo({top:0,behavior:'smooth'});
 notify('Your email is confirmed, but no application has been sent yet. Complete the form to send it for approval.');
}
async function showApplicationStatus(token,err,email,carried){
 // A verified applicant who is not yet approved sees the administrator's decision and can reply.
 let info=null;
 try{info=await api('/api/public/practitioner-application/status',{method:'POST',body:{access_token:token}})}catch{}
 if(info&&info.phase==='none'&&/No practitioner application was found/i.test(err.message)&&String(email||'').toLowerCase()!==OWNER_EMAIL){routeToApplicationForm(email,carried);return}
 if(!info||info.phase==='none'||info.phase==='approved')throw err;
 const body=info.can_update
  ?'<form class="stack" data-form="application-update"><label>Qualifications<textarea name="qualifications" rows="3" required minlength="4" maxlength="1000">'+esc(info.qualifications)+'</textarea></label>'+
   '<div class="field-grid"><label>Years of experience<input name="experience_years" type="number" min="0" max="70" required value="'+Number(info.experience_years||0)+'"></label>'+
   '<label>Specialisms<input name="specialisms" maxlength="250" value="'+esc(info.specialisms)+'"></label></div>'+
   '<label>Answer the administrator and add links to supporting documents<textarea name="achievements" rows="4" maxlength="1500">'+esc(info.achievements)+'</textarea></label>'+
   '<div class="form-actions">'+button('Close','modal-close','btn')+'<button class="btn primary" type="submit">Send updated application</button></div></form>'
  :'<div class="form-actions">'+button('Close','modal-close','btn')+'</div>';
 openDialog('Your practitioner application','<p>'+esc(info.message)+'</p>'+body);
}
function authHelpButtons(){
 if(!state.supabaseAuth)return '';
 return '<div class="spps-auth-options">'+button('Forgot Password?','spps-reset-request','btn small')+
  '<p class="small muted">Email verification and administrator approval are required for workspace access.</p></div>';
}
// v2 phase 1: the application is stored on the server at sign-up. Whichever browser (or the Android app) first
// holds the confirmed session promotes it; nothing is retyped. The photo can be added afterwards.
async function promoteServerApplication(){
 const sb=await requireSupabase();
 const {data:sessionData}=await sb.auth.getSession();
 const token=sessionData?.session?.access_token;if(!token)return null;
 return api('/api/public/practitioner-application/promote',{method:'POST',body:{access_token:token}});
}
function v2PhotoCard(){return `<section class="card register-card" data-v2-photo><h2>Add your profile photo</h2><p>Your email is confirmed and your application has been received. Add a profile photo to complete it.</p><form data-form="v2-application-photo" class="stack"><div class="field"><label>Profile photo<input name="photo" type="file" accept="image/jpeg,image/png,image/webp" required></label></div><button class="btn primary block" type="submit">Save photo and finish →</button></form></section>`}
function v2RegistrationOpsCard(){
 const s=state.registrationSummary;if(!s)return '';
 const confirmed=s.confirmed_no_application||[],failures=s.email_failures||[];
 return `<section class="card" data-v2-registration-ops><div class="between"><h2>Registrations ${badge(String(s.new_applications||0)+' new','gold')}</h2></div>`+
  `<h3>Confirmed, no application yet ${badge(String(confirmed.length))}</h3><p class="small muted">These people confirmed their email but have not sent an application. You may contact them.</p>`+
  (confirmed.length?confirmed.map(c=>`<div class="strip" data-confirmed-no-application><b>${esc(c.email)}</b> · confirmed ${esc(when(c.confirmed_at))}</div>`).join(''):'<p class="small muted">Nobody is waiting.</p>')+
  `<h3>Emails not delivered ${badge(String(failures.length),failures.length?'gold':'')}</h3>`+
  (failures.length?failures.map(f=>`<div class="strip" data-email-failure="${Number(f.id)}"><b>${esc(f.name||('#'+f.practitioner_id))}</b> · ${esc({approved:'Approval',rejected:'Rejection',request_more_info:'More information'}[f.kind]||f.kind)} email · ${esc(f.reason==='mail_not_configured'?'mail is not configured':'mail server error')} · ${Number(f.attempts)} attempt(s) ${button('Resend','v2-email-resend','btn small',`data-id="${Number(f.id)}"`)}</div>`).join(''):'<p class="small muted">All review emails were delivered.</p>')+
  `</section>`}
async function loadRegistrationSummary(){if(!state.user?.is_admin){state.registrationSummary=null;return}try{state.registrationSummary=await api('/api/admin/registrations/summary')}catch{state.registrationSummary=null}}
function adminApprovalPage(){
 if(!state.user?.is_admin)return pageHead('Access denied','Only the platform administrator may review practitioner applications.');
 const filter=state.adminFilter||'pending';
 const others=(state.adminReviews||[]).filter(r=>!r.is_owner_account&&!r.self);
 const bucket=r=>r.status==='approved'?'approved':(r.status==='rejected'?'rejected':'pending');
 const counts={pending:0,approved:0,rejected:0};others.forEach(r=>{counts[bucket(r)]++});
 const rows=others.filter(r=>filter==='all'||bucket(r)===filter);
 const tabs=[['pending','Awaiting decision',counts.pending],['approved','Approved',counts.approved],['rejected','Rejected',counts.rejected],['all','All',others.length]];
 const phaseLabel=r=>({pending:'New application',more_info:'Waiting for applicant reply',responded:'Applicant replied - ready to review',rejected:'Rejected',approved:r.listed?'Approved and listed':'Approved, not listed (profile edited)'})[r.phase]||esc(r.status);
 const actions=(r)=>{const id=Number(r.practitioner_id);const b=(label,decision,cls)=>button(label,'spps-admin-decision','btn small '+cls,`data-id="${id}" data-decision="${decision}"`);
  if(r.status==='approved')return `<div class="inline-actions">${r.listed?'':b('Re-approve listing','approve','primary')}${b('Suspend and ask for info','request_more_info','')}${b('Reject and remove','reject','danger')}</div>`;
  if(r.status==='rejected')return `<div class="inline-actions">${b('Approve','approve','primary')}${b('Reconsider (ask for info)','request_more_info','')}</div>`;
  return `<div class="inline-actions">${b('Approve','approve','primary')}${b('Request more info','request_more_info','')}${b('Reject','reject','danger')}</div>`};
 const tabButtons=tabs.map(([key,label,n])=>button(`${label} (${n})`,'spps-admin-filter','btn small'+(filter===key?' primary':''),`data-filter="${key}"`)).join('');
 return pageHead('Admin Dashboard','Private owner-only practitioner registration review. Nobody is listed in Find a Sport Psychologist or given workspace access until you approve them.')+
  v2RegistrationOpsCard()+(window.SPPSv2?SPPSv2.adminCards():'')+`<section class="card"><div class="between"><h2>Practitioner applications</h2>${button('Refresh','spps-refresh-admin','btn small')}</div><div class="inline-actions" role="group" aria-label="Filter applications">${tabButtons}</div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Applicant</th><th>Qualifications / claims</th><th>Email</th><th>Status</th><th>Decision</th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${esc(r.name)}</strong><p class="small">${esc(r.email)}</p></td><td><p><b>Qualifications</b>: ${esc(r.qualifications||'Not supplied')}</p><p><b>Experience</b>: ${Number(r.experience_years)||0} years</p><p><b>Specialisms</b>: ${esc(r.specialisms||'Not provided')}</p><p><b>Achievements / explanations</b>: ${esc(r.achievements||'Not provided')}</p></td><td style="white-space:nowrap">${r.email_verified?'Verified':'Not verified'}</td><td>${r.phase==='responded'?badge(phaseLabel(r),'gold'):esc(phaseLabel(r))}${r.review_note?`<p class="small">Your note: ${esc(r.review_note)}</p>`:''}</td><td>${actions(r)}</td></tr>`).join('')||'<tr><td colspan="5">Nothing in this list.</td></tr>'}</tbody></table></div></section>`;
}
function awaitingCount(){const s=state.registrationSummary;if(s&&typeof s.new_applications==='number')return s.new_applications;return (state.adminReviews||[]).filter(r=>!r.is_owner_account&&!r.self&&r.status==='pending_approval'&&!r.more_info_requested).length}

function header(){const inWorkspace=Boolean(state.user)&&!['directory','public','register'].includes(state.mode);return `<div class="v1-social-bar"><div class="shell">${socials()}</div></div><header class="site-header site-header-v1"><div class="shell">
  ${brand()}<span class="spps-release-badge">SPPS Version 1.0 - Free</span>${inWorkspace?'':`<nav class="site-links" aria-label="Main navigation"><a href="/" data-action="directory">Home</a><a href="/#practitioner-directory">Find a Practitioner</a><a href="/#features">Features</a><a href="/#dual-portals">Dual Portals</a><a href="/#about">About</a><a href="/#account-approval">Account Approval</a></nav>`}
  <div class="site-actions">${themePick()}<div class="access-actions">${inWorkspace?button('Settings','view','btn small primary','data-view="settings"')+button('Logout','logout','btn small'):state.user?button('My workspace','studio','btn small primary'):button('Practitioner login','studio','btn small primary')}${!inWorkspace&&!state.user?button('Join free','registration','btn small'):''}</div></div>
 </div></header>`}

function footer(){return `<footer class="footer"><div class="shell"><span>© ${new Date().getFullYear()} SPPS Version 1.0 - Free · WinMindPerform</span><span><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/policies">Policies</a> · <a href="/guide">How to use SPPS</a> · Not an emergency service</span></div></footer>`}

const heroGraphic=()=>`<div class="illustration" aria-hidden="true"><div class="halo"><span class="orb a"></span></div><div class="halo two"><span class="orb b"></span><span class="orb c"></span></div><div class="float-card left">◆ Practitioner-led care</div><div class="hero-card"><div class="mini-logo"><b>Progress at a glance</b><span>◈</span></div><p class="small">Illustrative workflow preview</p><div class="metric"><div class="metric-cell">Profile<br><b>01</b></div><div class="metric-cell">Plan<br><b>02</b></div><div class="metric-cell">Review<br><b>03</b></div></div><div class="trace-bars">${[23,36,29,51,41,59,54,72,68,77].map(x=>`<i style="height:${x}%"></i>`).join('')}</div><div class="small muted" style="margin-top:12px">One connected practitioner workflow</div></div><div class="float-card right"><strong>✓</strong> Focused sport psychology support</div></div>`;
function publicPage(){
 const services=(state.services||[]).map((item,i)=>`<article class="service-chip"><span class="service-dot ${['blue','pink','gold','teal'][i%4]}"></span><div><h3>${esc(item.title)}</h3><p>${esc(item.duration)}</p></div></article>`).join('');
 return `${header()}<main>
 <section class="hero hero-clean"><div class="shell hero-clean-grid"><div class="hero-copy"><span class="capsule">SPORT PSYCHOLOGY • PERFORMANCE • WELL-BEING</span><h1>Better support.<br><em>Stronger performance.</em></h1><p class="intro">Connect with qualified sport psychology practitioners for confidence, competition pressure, focus and return-to-sport support.</p><div class="hero-actions">${button('Connect with a Sport Psychologist <span aria-hidden="true">→</span>','directory','btn hero-cta')}</div><p class="hero-note">For athletes, parents and organisations · No account required to browse</p></div><div class="hero-art" aria-hidden="true"><div class="art-orbit one"></div><div class="art-orbit two"></div><div class="art-coin"><svg viewBox="0 0 180 180"><path d="M18 97H55L75 56L102 122L124 84L140 97H163" fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg></div><i class="art-spark a"></i><i class="art-spark b"></i></div></div></section>
 <section class="public-services" id="services"><div class="shell"><div class="between section-small-head"><div><span class="eyebrow">What we can help with</span><h2>Professional support for sporting goals.</h2></div><a href="/practitioners" data-action="directory" class="text-link">Browse practitioners →</a></div><div class="service-chips">${services||'<p>Services unavailable</p>'}</div></div></section>
 <section class="simple-steps"><div class="shell"><span class="eyebrow">How it works</span><div class="steps-compact"><div><span>01</span><b>Explore profiles</b><p>Review published qualifications and experience.</p></div><div><span>02</span><b>Choose a time</b><p>Request an available consultation slot.</p></div><div><span>03</span><b>Meet your practitioner</b><p>Access an appointment after practitioner approval.</p></div></div></div></section>
 <div class="shell public-disclaimer"><span>SPPS Version 1.0 - Free. Listings require credential review and administrator approval. Not crisis care or a substitute for medical diagnosis or clearance.</span><span><a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></span></div>
 </main>${footer()}`;
}

function freeSocialBar(){return `<div class="spps-social-bar"><div class="shell spps-social-inner"><span>SPPS Version 1.0 - Free</span><nav aria-label="Social media"><a href="https://www.instagram.com/win_mind_1/" target="_blank" rel="noopener noreferrer" aria-label="Instagram">◎ Instagram</a><a href="https://www.linkedin.com/in/dr-karanbir-singh-ph-d-82163b54/" target="_blank" rel="noopener noreferrer" aria-label="LinkedIn">in LinkedIn</a><a href="https://www.facebook.com/share/1FLf6LdARm/" target="_blank" rel="noopener noreferrer" aria-label="Facebook">f Facebook</a><a href="https://www.threads.com/@win_mind_1" target="_blank" rel="noopener noreferrer" aria-label="Threads">@ Threads</a><a href="https://open.spotify.com/show/6UjTBFncbZ3tuyM3FfNfSc" target="_blank" rel="noopener noreferrer" aria-label="Spotify">≋ Spotify</a></nav></div></div>`}

function freeDirectoryPage(){
 const profiles=state.practitioners||[];
 return `${header()}<main>
 <section class="spps-free-hero"><div class="shell spps-hero-grid">
  <div class="spps-hero-overview">
   <div class="eyebrow">SPORT PSYCHOLOGY PRACTITIONER SUITE · VERSION 1.0</div>
   ${svgLogo()}
   <h1>Sport psychology support, <em>more accessible.</em></h1>
   <p class="intro">Discover verified sport psychology practitioners. Practitioners can manage athletes, monitoring, assessments, injury psychology, bookings and video consultations in one workspace.</p>
   <p class="capsule">Free registration · Free appointments · No subscriptions</p>
   <div class="hero-actions"><a class="btn primary" href="#find-psychologist">Find a Sport Psychologist</a><a class="btn" href="/portal">Athlete / Guardian Portal</a></div>
  </div>
  <aside class="spps-hero-access">
   <h2>Practitioner access</h2><p>Register free or sign in to manage your practice. Profiles are listed only after independent credential verification and publication approval.</p>
   ${button('Practitioner login','studio','btn primary block')}
   ${button('Create free practitioner account','registration','btn block')}
   <hr><h3>Are you an athlete or guardian?</h3>
   <a class="btn block" href="/portal">Open client portal</a>
   <p class="small muted">Professional details and appointment messages are private. No clinical records appear in the directory.</p>
  </aside>
 </div></section>
 <section id="find-psychologist" class="section"><div class="shell"><div class="section-head"><div class="eyebrow">FIND A SPORT PSYCHOLOGIST</div><h2>Practitioner directory</h2><p>Browse approved profiles. Private email addresses, phone numbers and clinical records are never published.</p></div>
 <div class="spps-practitioner-grid">${profiles.length?profiles.map(pr=>`<article class="spps-practitioner-card"><div class="tile-top"><img src="${esc(pr.photo_url)}" alt="Portrait of ${esc(pr.name)}" loading="lazy" width="92" height="92"><h3>${esc(pr.name)}</h3>${pr.featured_owner?'<span class="spps-founder-label">Founder · Owner-managed listing</span>':''}</div>${pr.role?`<p class="spps-founder-role">${esc(pr.role)}</p>`:''}<p><strong>Qualifications</strong> · ${esc(pr.qualifications)}</p><p><strong>Experience</strong> · ${esc(pr.experience_years)}${pr.featured_owner?'+':''} years</p><p><strong>Specialisms</strong> · ${esc(pr.specialisms||'Sport psychology')}</p><p><strong>Achievements</strong> · ${esc(pr.achievements||'Not provided')}</p><p class="small">${pr.featured_owner?'Owner-managed founder profile':pr.credentials_verified?'Credentials verified by SPPS':'Credential review pending'}</p>${button('Request an appointment','free-contact','btn primary block',`data-id="${pr.id}"`)}</article>`).join(''):'<div class="empty">No independently verified practitioner profiles are currently published.</div>'}</div>
 </div></section></main>${footer()}`;
}
function freeRegistrationPage(){
 return `${header()}<main class="shell register-shell"><div class="registration-heading"><span class="eyebrow">FREE PRACTITIONER REGISTRATION</span><h1>Create your SPPS account</h1><p>All workspace tools are available free. Listing remains pending until independent credential verification.</p></div><div class="card register-card"><form class="stack" data-form="practitioner-register"><input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px">
 <div class="field-grid"><div class="field"><label>Full professional name</label><input name="name" minlength="3" maxlength="120" required></div><div class="field"><label>Professional email (private)</label><input type="email" name="email" required autocomplete="email"></div><div class="field"><label>Password (10+ characters)</label><input type="password" name="password" minlength="10" required autocomplete="new-password"></div><div class="field"><label>Professional portrait</label><input type="file" name="photo" accept="image/jpeg,image/png,image/webp" required></div><div class="field"><label>Years of experience</label><input type="number" name="experience_years" min="0" max="70" value="0" required></div></div>
 <div class="field"><label>Qualifications</label><textarea name="qualifications" rows="3" minlength="4" required></textarea></div><div class="field"><label>Achievements</label><textarea name="achievements" rows="3"></textarea></div><div class="field"><label>Specialisms</label><input name="specialisms" maxlength="250"></div>
 <label class="checkline"><input type="checkbox" name="terms_accepted" required> I accept the <a href="/terms" target="_blank">Terms of Use</a> and <a href="/privacy" target="_blank">Privacy Policy</a>.</label>
 <button type="submit" class="btn primary block">Create my free account</button><p class="small muted">No payment information is collected. SPPS must verify your professional credentials before your public profile is listed.</p>
 </form></div></main>${footer()}`;
}
function freeBookingsPage(){
 if(!state.portalEnabled)return `${pageHead('Appointments','Appointment booking is not switched on yet.',button('Manage client bookings','client-bookings','btn'))}`;
 return `${pageHead('Appointments','Times are shown in your local time zone.',button('Add availability','client-bookings','btn'))}${appointmentsPanel()}`;
}
function freeSettingsPage(){
 return `${pageHead('Profile & Privacy','Free practitioner account and clinical data controls.')}<div class="content-grid">${window.SPPSv2?SPPSv2.settingsCards():''}${window.SPPSPush?SPPSPush.cardHtml():''}<section class="card"><h3>Free Open Access</h3><p>All existing monitoring, assessments, reports, injury psychology, scheduling and video features are included at no charge.</p></section><section class="card"><h3>Professional profile</h3><p>Editing credentials temporarily removes a listing until reviewed.</p>${button('Edit qualifications','profile-edit','btn')}</section>${lifecycleCard()}</div>`;
}

function directoryPage(){
 const profiles=state.practitioners||[];
 const cards=profiles.map(pr=>`<article class="practitioner-tile v1-practitioner" data-search="${esc([pr.name,pr.specialisms,pr.qualifications,pr.achievements].join(' ').toLowerCase())}">
  <div class="tile-top"><img class="practitioner-photo" src="${esc(pr.photo_url)}" alt="Profile photo of ${esc(pr.name)}" loading="lazy" width="92" height="92"><div class="tile-identity"><h3>${esc(pr.name)}</h3>${pr.featured_owner?'<span class="spps-founder-label">Founder · Owner-managed listing</span>':''}</div></div>
  <div class="tile-details"><div class="tile-info"><span class="tile-icon">◈</span><div><b>Qualifications</b><p>${esc(pr.qualifications)}</p></div></div><div class="tile-info"><span class="tile-icon">◷</span><div><b>Experience</b><p>${Number(pr.experience_years||0)} years</p></div></div><p class="small">${esc(pr.achievements||'View their professional profile and request an introduction.')}</p></div>
  <div class="v1-tile-actions">${button('Book appointment','free-contact','btn primary tile-book',`data-id="${pr.id}"`)}</div>
 </article>`).join('');
 return `${header()}<main class="v1-public">
  <section class="v1-hero" aria-labelledby="v1-hero-title"><div class="shell v1-hero-grid">
    <div class="v1-hero-primary"><div class="v1-hero-brand"><img src="/assets/spps-v1-logo.png" class="v1-hero-logo" alt="Official SPPS logo — Sport Psychology Practitioner Suite" width="240" height="112"><span>SPORT PSYCHOLOGY PRACTITIONER SUITE</span></div><div class="v1-kicker">SPPS Version 1.0 - Free</div>
      <h1 id="v1-hero-title">Connecting Sport Psychology Practitioners and Athletes</h1>
      <p>One connected platform for practitioner profiles, athlete records, assessments, confidential notes, interventions, progress monitoring and secure appointments.</p>
      <div class="v1-actions">${button('Connect with a Sports Psychologist →','directory-scroll','btn v1-cta')}${button('Join as a practitioner — free','registration','btn v1-outline')}</div>
      <div class="v1-hero-foot"><span>Free for practitioners and athletes</span><span>Consent-led access</span><span>Practitioner-led decisions</span></div>
    </div>
    <div class="v1-hero-access"><div class="v1-auth-box"><div class="v1-auth-top"><span>YOUR CONNECTED PRACTICE</span><span class="v1-free-tag">Free v1.0</span></div>
      <h2>Practitioner portal</h2><p>Manage your athletes, records and appointments.</p>
      ${state.user?`${button('Open my workspace →','studio','btn primary block')}`:`<form class="v1-login" data-form="login"><label>Email<input name="email" type="email" placeholder="your@professional.email" required autocomplete="username"></label><label>Password<input name="password" type="password" required autocomplete="current-password" placeholder="Password"></label><label class="checkline"><input type="checkbox" name="terms_accepted" required><span class="check-copy">I accept the <a href="/terms" target="_blank" rel="noopener">terms</a> and <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</span></label><button type="submit" class="btn primary block">Login to Practitioner Portal →</button></form>`}
      <div class="v1-auth-links"><a href="/register" data-action="registration">New practitioner? Register free</a><a href="/portal">Athlete or guardian portal →</a></div>
    </div></div>
  </div></section>
  <section id="practitioner-directory" class="directory-content v1-directory"><div class="shell"><div class="v1-directory-heading"><div><div class="eyebrow">Professional discovery</div><h2>Find a Sport Psychologist</h2><p>Browse and connect with qualified sport psychology practitioners with expertise across a range of sports, performance levels and athlete needs.</p></div><div class="v1-search"><label for="spps-practitioner-search">Search professionals</label><input id="spps-practitioner-search" type="search" placeholder="Name, qualification, specialism..." autocomplete="off"></div></div>
  <div id="v1-directory-cards" class="practitioner-grid">${cards||'<div class="empty">No verified practitioner profiles are published yet. Practitioners can register free and submit credentials for review.</div>'}</div><p id="v1-directory-empty" class="small muted" hidden>No profiles match that search.</p><p class="directory-safety">Profiles are published only after independent credential verification and approval. A directory listing is not a guarantee of clinical qualification or treatment suitability. No clinical records are publicly displayed.</p></div></section>
  <section class="v1-benefits" id="features" aria-label="What you can do"><div class="shell v1-benefit-grid" id="dual-portals">${[['Practitioner Workspace','Manage athletes, cases and confidential notes'],['Athlete/Guardian Connection','Consent-based links and appointment requests'],['Case Records','Practitioner-owned documentation and review'],['Intervention Programmes','Assign exercises and monitor progress'],['Progress Tracking','Assessments, check-ins and visual trends'],['Secure Video Sessions','Practitioner-approved consultations']].map(([h,d],i)=>`<div class="v1-benefit"><span class="v1-benefit-icon">${['◈','◎','▥','✳','◉','▣'][i]}</span><b>${h}</b><small>${d}</small></div>`).join('')}</div></section>
  <section class="v1-editorial" id="about"><div class="shell"><h2>About SPPS</h2><p>Sport Psychology Practitioner Suite is an independent WinMindPerform platform connecting practitioners, athletes and guardians through consent-based support, case management and progress tracking. Access to Version 1.0 is free.</p></div></section>
   <section class="v1-editorial v1-validation" id="account-approval"><div class="shell"><h2>Account Approval and Safety</h2><p>SPPS Version 1.0 - Free. Practitioner accounts and public directory listings require administrator approval and qualification review. This service is not emergency care or a substitute for a qualified clinician.</p><p><a href="/terms">Terms of Use</a> · <a href="/privacy">Privacy Policy</a> · <a href="/policies">Policies</a> · <a href="/guide">How to use SPPS</a></p></div></section>
   </main>${footer()}`;
}

function registrationPage(){return `${header()}<main class="shell register-shell"><div class="registration-heading"><span class="eyebrow">SPPS Version 1.0 - Free</span><h1>Create your practitioner account</h1><p>Applications are free, but you cannot enter the clinical workspace or appear in the directory until the platform administrator verifies and approves your credentials.</p>${state.emailVerifiedNotice?`<div class="login-notice application-missing-notice" role="status"><strong>Your email is confirmed, but your application has not been sent yet.</strong><p>${state.carriedApplication?'Your details from sign-up are filled in below. Add your photo and press “Send application for approval”.':'Fill in your details below, add your photo and press “Send application for approval”.'} If you have already sent your application, sign in instead.</p></div>`:''}</div>
 ${state.photoAfterConfirm?v2PhotoCard():''}<div class="register-layout"${state.photoAfterConfirm?' hidden':''}><section class="card register-card"><h2>Professional details</h2><form data-form="practitioner-register" class="stack"><input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px"><div class="field-grid"><div class="field"><label>Full name<input name="name" minlength="3" maxlength="120" required value="${esc(state.carriedApplication?.name||'')}"></label></div><div class="field"><label>Professional email<input name="email" type="email" value="${esc(state.supabaseEmail||'')}" required autocomplete="email"></label></div><div class="field"><label>Password (10+ characters)<input name="password" type="password" minlength="10" ${state.supabaseAuth&&state.supabaseEmail?'':'required'} autocomplete="new-password"></label></div><div class="field"><label>Profile photo<input name="photo" type="file" accept="image/jpeg,image/png,image/webp"></label></div><div class="field"><label>Years of experience<input name="experience_years" type="number" min="0" max="70" required value="${Number(state.carriedApplication?.experience_years)||0}"></label></div><div class="field"><label>Specialisms<input name="specialisms" maxlength="250" placeholder="Attention, injury psychology, confidence..." value="${esc(state.carriedApplication?.specialisms||'')}"></label></div></div><div class="field"><label>Qualifications<textarea name="qualifications" minlength="4" rows="3">${esc(state.carriedApplication?.qualifications||'')}</textarea></label></div><div class="field"><label>Experience and professional achievements<textarea name="achievements" maxlength="1500" rows="3">${esc(state.carriedApplication?.achievements||'')}</textarea></label></div><label class="checkline"><input type="checkbox" name="terms_accepted" required><span class="check-copy">I accept the <a href="/terms" target="_blank" rel="noopener">terms</a> and <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>. My qualifications may be checked before public listing.</span></label><button class="btn primary block" type="submit">${state.supabaseAuth&&state.supabaseEmail?'Send application for approval →':'Create free account →'}</button></form></section>
 <aside class="card register-aside"><img src="/assets/spps-v1-logo.png" alt="SPPS logo" class="v1-side-logo"><h3>Included at no charge</h3><p>Athlete records, consultations, assessment tracking, intervention activities, reporting tools and a linked athlete/guardian portal.</p><p class="small">There is no subscription checkout, booking commission or payment gateway in SPPS 1.0.</p><p class="small">Profile publication requires independent review. Registration does not confer any professional certification.</p></aside></div>
 </main>${footer()}`}

function loginPage(){return `${header()}<main class="login-layout"><div class="login-aside">${svgLogo()}<div class="eyebrow" style="color:#f0c97e">SPPS Version 1.0 - Free</div><h1>Everything you need to support your athletes, in one place.</h1><p>Record. Assess. Intervene. Review. A connected, practitioner-led workflow separated from the public directory.</p><div class="hero-trust" style="color:#d4eaf1"><span>Confidential records</span><span>Clear clinical ownership</span><span>Controlled access</span></div></div><div class="login-form-wrap"><div class="login-form"><div class="eyebrow">Practitioner access</div><h2>Welcome back</h2>${state.loginNotice?`<div class="login-notice" role="status"><strong>${esc(state.loginNotice.title)}</strong><p>${esc(state.loginNotice.text)}</p></div>`:''}<p>Sign in to review appointment requests and continue your athlete cases.</p>${state.portalEnabled?'<p class="small" data-portal-entry>Athlete, parent or guardian? <a href="/portal">Open the athlete &amp; guardian portal</a>.</p>':''}<div class="card"><form data-form="login" class="stack"><div class="field"><label for="login-email">Email</label><input id="login-email" name="email" type="email" autocomplete="username" required placeholder="you@example.com"></div><div class="field"><label for="login-pass">Password</label><input id="login-pass" name="password" type="password" autocomplete="current-password" required></div><label class="checkline"><input type="checkbox" name="terms_accepted" required><span class="check-copy">I acknowledge the <a target="_blank" rel="noopener" href="/terms">Terms of Use</a>, <a target="_blank" rel="noopener" href="/privacy">Privacy Policy</a>, and account approval requirements.</span></label><button class="btn primary block" type="submit">Sign in securely →</button></form>${state.supabaseAuth?`<p class="small">Email verification and administrator approval are required.</p>${authHelpButtons()}`:''}${state.passwordRecovery?`<form data-form="spps-change-password" class="stack"><label>New password<input name="password" type="password" autocomplete="new-password" minlength="12" required></label><button class="btn primary block" type="submit">Update password</button></form>`:''}<p class="small" style="margin:20px 0 0">New practitioner? <a href="/register" data-action="registration">${state.freeAccess?'Register free':'Register and choose a paid plan'}</a>.</p></div></div></div></main>`}
const NAV=[['overview','▦','Overview'],['bookings','▦','Appointments'],['athletes','♙','Athletes & Cases'],['assessments','◫','Assessments & Trends'],['monitoring','◉','Monitoring'],['analytics','▥','Load & Recovery'],['injury','♡','Injury Psychology'],['interventions','✳','Interventions'],['reports','▤','Reports'],['settings','⚙','Settings']];
function sidebar(){return `<aside class="app-nav" aria-label="Practitioner sections"><div class="nav-title">Practice workspace</div>${(state.user?.is_admin?[...NAV,...(window.SPPSv2?SPPSv2.nav:[]),['admin','⚑','Admin Dashboard']]:[...NAV,...(window.SPPSv2?SPPSv2.nav:[])]).map(([v,i,label])=>`<button class="nav-link ${state.view===v?'active':''}" data-action="view" data-view="${v}"><span class="nav-icon">${i}</span>${label}</button>`).join('')}${button('Logout','logout','nav-link workspace-logout')}<div class="bottom"><div class="small muted" style="padding:8px 10px">SPPS Version 1.0 - Free</div><div class="small muted" style="padding:0 10px">${esc(state.user?.name||'')}</div></div></aside>`}
function ownerDashboardSwitcher(){
  if(!state.user?.is_admin)return '';
  const admin=state.view==='admin';
  return '<nav class="spps-dual-dashboard" data-testid="dual-dashboard-switcher" aria-label="Choose owner dashboard">'+
   '<div class="spps-dual-dashboard-copy"><span class="eyebrow">SPPS OWNER ACCESS</span><strong>Switch workspace</strong><span>Platform administration and your private practitioner caseload are separate views.</span></div>'+
   '<div class="spps-dual-dashboard-tabs" role="group" aria-label="Owner workspace views">'+
   button('Practitioner Workspace','view','spps-dashboard-tab'+(admin?'':' active'),'data-view="overview" aria-pressed="'+(!admin)+'"')+
   button('Admin Dashboard'+(awaitingCount()?' '+badge(String(awaitingCount())+' waiting','gold'):''),'view','spps-dashboard-tab'+(admin?' active':''),'data-view="admin" aria-pressed="'+admin+'"')+
   '</div></nav>';
}
function pageHead(title,subtitle,action=''){return `<div class="page-head"><div><div class="eyebrow">SPPS Version 1.0 - Free / ${esc(title)}</div><h1>${esc(title)}</h1><p>${subtitle}</p></div>${action}</div>`}
function dashboardPage(){const st=state.dashboard?.stats||{};const incoming=(state.bookings||[]).filter(b=>['awaiting_payment','paid_pending_review'].includes(b.status));const prog=(state.programmeEnquiries||[]).filter(q=>q.status==='new');return `${pageHead('Overview','Your caseload and incoming service requests, without a separate athlete portal.',`<div class="inline-actions">${button('My public profile','my-public-profile','btn')}${button('Refresh requests','refresh-data','btn')}${state.portalEnabled?button('Client bookings','client-bookings','btn'):''}${state.user?.is_admin?button('Practitioner reviews','practitioner-reviews','btn'):''}${button('+ New athlete','new-athlete','btn primary')}</div>`)}<section class="card booking-alert"><div class="between"><div><h3>Incoming booking requests ${badge(String(incoming.length),'gold')}</h3><p class="small muted">Availability slots are public offers, not assigned clients. A named booking appears here once a client requests your slot; approval requires practitioner confirmation.</p></div>${button('Open bookings','open-bookings','btn small primary')}</div>${incoming.slice(0,4).map(b=>`<div class="strip"><b>${esc(b.contact_name)}</b> · ${esc(b.status)} · ${when(b.starts_at)} · ₹${(b.amount_paise/100).toFixed(0)}</div>`).join('')||'<p class="muted small">No outstanding slot requests.</p>'}<p class="small muted">New programme enquiries: ${prog.length}. Use Refresh to check the inbox.</p></section><div class="kpi-grid">${[['Athletes',st.athletes||0],['Active cases',st.active_cases||0],['Pending appointment requests',incoming.length],['Open drafts',st.open_drafts||0]].map(([l,n])=>`<div class="kpi"><div class="eyebrow">${l}</div><div class="kpi-num">${n}</div><div class="small muted">Practice overview</div></div>`).join('')}</div><div class="content-grid"><section class="card"><div class="between"><h3>Appointment requests</h3>${button('Open appointments','view','btn small','data-view="bookings"')}</div>${incoming.length?`<div class="list-row"><div class="spacer"><h4>${incoming.length} request${incoming.length===1?'':'s'} waiting for your decision</h4><p>Approve, decline or propose another time in Appointments.</p></div></div>`:'<div class="empty">No pending appointment requests.</div>'}</section><section class="card"><div class="eyebrow">Connected workflow</div><h3>From appointment to progress review</h3><div class="stack"><div class="strip">01 ◆ Appointment request from the directory</div><div class="strip">02 ◆ Independent intake consent</div><div class="strip">03 ◆ Athlete case and goals</div><div class="strip">04 ◆ Assessment and intervention</div><div class="strip">05 ◆ Review and report draft</div></div><p class="foot-note">A booking request never grants access to confidential case records. Reports remain private drafts until independently reviewed.</p></section></div>`}
function athletesPage(){const a=state.athleteDetail?.athlete,cases=state.athleteDetail?.cases||[];return `${pageHead('Athletes & Cases','The practitioner owns the case workflow. Athletes do not need accounts.',`<div class="inline-actions">${button('Refresh','refresh-data','btn')}${button('+ New athlete','new-athlete','btn primary')}</div>`)}<div class="athlete-layout"><section class="athlete-list">${state.athletes.length?state.athletes.map(x=>`<button class="list-btn ${state.athleteId===x.id?'selected':''}" data-action="athlete" data-id="${x.id}" aria-label="Open overview for ${esc(x.display_name)}"><b>${esc(x.display_name)}</b><span>${esc(x.sport)} · ${esc(x.code)}</span></button>`).join(''):'<div class="empty">No athletes yet.</div>'}</section><section class="athlete-panel" id="athlete-overview-panel" aria-label="Athlete overview" data-athlete-id="${a?.id??''}">${a?`<div class="card"><div class="between"><div><span class="eyebrow">Athlete overview · ${esc(a.code)} · ${esc(a.sport)}</span><h2 style="margin:6px 0 4px">${esc(a.display_name)}</h2><span class="muted small">${esc(a.athlete_group)}</span></div>${a.service_consent_recorded?button('+ New case','new-case','btn primary'):button('Record service consent','record-consent','btn primary')}</div><div class="inline-actions">${button("Assessment history","athlete-assessments","btn small")}${button("Monitoring history","athlete-monitoring","btn small")}${state.portalEnabled?button("Portal access","portal-access","btn small")+button("Programme & sharing","participation","btn small"):''}${window.SPPSv2?SPPSv2.athleteButtons(a):''}</div><div class="case-tabs" style="margin-top:18px">${cases.map(c=>`<button class="${state.caseId===c.id?'selected':''}" data-action="case" data-id="${c.id}">${esc(c.title)}</button>`).join('')}</div>${state.caseDetail?casePanel():`<div class="empty">Select a case or create one to document services.</div>`}</div>`:'<div class="empty">Choose an athlete above to open their overview and case history.</div>'}</section></div>`}
function casePanel(){const c=state.caseDetail.case,notes=state.caseDetail.notes||[],as=state.caseDetail.assignments||[];return `<div class="between"><div><h3>${esc(c.title)}</h3><p class="small">${esc(c.category)} · Review: ${esc(c.next_review||'not scheduled')}</p></div>${badge(c.status,'teal')}</div><p style="margin:8px 0"><b>Working goal:</b> ${esc(c.goal||'Not recorded')}</p><div class="inline-actions">${button('+ Consultation note','new-note','btn small primary')}${button('+ Score / check-in','new-observation','btn small')}${button('+ Programme','assign-intervention','btn small')}${button('+ Report draft','new-report','btn small')}</div><h3 style="margin-top:25px">Consultation documentation</h3>${notes.length?notes.map(n=>`<div class="note-box"><div class="between"><strong>${esc(n.note_date)} · ${esc(n.state.toUpperCase())}</strong>${n.state==='draft'?button('Finalise','finalize-note','btn small',`data-id="${n.id}" data-revision="${n.revision}"`):badge('Immutable final')}</div><div style="margin-top:8px">${esc(n.content)}</div></div>`).join(''):'<div class="empty">No consultation notes yet.</div>'}<h3 style="margin-top:25px">Assigned interventions</h3>${as.length?as.map(x=>`<div class="strip"><b>${esc(x.intervention)}</b> · ${esc(x.dosage)} · ${esc(x.status)}</div>`).join(''):'<div class="empty">No interventions assigned.</div>'}`}
function graphSVG(entries,lo,hi){const width=660,height=198,L=42,R=18,T=14,B=32,w=width-L-R,h=height-T-B; if(!entries.length)return '<div class="empty">No observations yet. Add an observation to begin a trend.</div>';
 const ordered=[...entries].sort((a,b)=>a.observed_on.localeCompare(b.observed_on)||a.id-b.id);
 const x=i=>L+(ordered.length<2?w/2:i*w/(ordered.length-1));const y=v=>T+h-(v-lo)/(hi-lo)*h;
 const points=ordered.map((d,i)=>`${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(' ');
 return `<div class="chart-area" role="img" aria-label="Graph of ${esc(entries[0].label)}; ${ordered.length} observations"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><line x1="${L}" x2="${width-R}" y1="${T+h}" y2="${T+h}"/><line x1="${L}" x2="${L}" y1="${T}" y2="${T+h}"/><text x="4" y="${T+5}">${esc(hi)}</text><text x="4" y="${T+h}">${esc(lo)}</text>${ordered.length>1?`<polyline points="${points}"/>`:''}${ordered.map((d,i)=>`<circle cx="${x(i)}" cy="${y(d.value)}" r="5"><title>${esc(d.observed_on)}: ${esc(d.value)}</title></circle>`).join('')}<text x="${L}" y="${height-8}">${esc(ordered[0].observed_on)}</text><text x="${width-R-80}" y="${height-8}">${esc(ordered[ordered.length-1].observed_on)}</text></svg></div>`}
function clinicalAthletePicker(){const chosen=state.athletes.find(a=>a.id===state.athleteId),cases=state.athleteDetail?.cases||[],caseLabel=cases.find(c=>c.id===state.caseId)?.title;
 return state.athletes.length?`<div class="field" style="max-width:440px;margin:12px 0"><label>Current athlete (shared server record)</label><select data-action="athlete-select" aria-label="Selected athlete"><option value="">Choose athlete</option>${state.athletes.map(a=>`<option value="${a.id}" ${a.id===state.athleteId?'selected':''}>${esc(a.display_name)} — ${esc(a.sport)} (${esc(a.code)})</option>`).join('')}</select>${chosen?`<label for="clinical-case-select">Case for assessment and monitoring</label><select id="clinical-case-select" data-action="case-select" aria-label="Selected case"><option value="">${cases.length?'Choose a case':'No case created yet'}</option>${cases.map(c=>`<option value="${c.id}" ${c.id===state.caseId?'selected':''}>${esc(c.title)}</option>`).join('')}</select><div class="inline-actions">${button('Open athlete overview','open-athlete-overview','btn small')}${!cases.length?button('+ Create case','new-case','btn small primary'):''}</div>`:''}<p class="small muted">${chosen?`Selected: ${esc(chosen.display_name)}${caseLabel?` · ${esc(caseLabel)}`:' · Create or select a case before adding an assessment.'}`:'Choose an athlete before saving.'} Records are saved to the shared server.</p></div>`:'<p class="small muted">Create an athlete record and case only after consent and authorization.</p>';
}
function factorPage(kind){const isAssess=kind==='assessment',available=state.factors.filter(f=>f.kind===(isAssess?'assessment':'monitoring'));const observations=state.observations.filter(o=>o.kind===(isAssess?'assessment':'monitoring'));
 const groups=new Map();for(const o of observations){const k=`${o.factor_id}:v${o.version}`;if(!groups.has(k))groups.set(k,[]);groups.get(k).push(o)}
 return `${pageHead(isAssess?'Assessments':'Monitoring',isAssess?'Formal and offline score factors are recorded separately from daily monitoring.':'Short, adaptable check-ins and dated observations, not automatic clinical diagnoses.',`<div class="flex">${button('+ Define factor','new-factor','btn')}${button('+ Record score','new-observation','btn primary')}${button('Refresh records','refresh-clinical','btn')}</div>`)}<div class="content-grid"><section class="card"><h3>${isAssess?'Assessment scores by factor':'Longitudinal monitoring'}</h3>${clinicalAthletePicker()}${groups.size?[...groups].map(([key,rows])=>{const first=rows[0];return `<div style="margin:22px 0"><div class="between"><h3>${esc(first.label)} ${badge(first.group_name)}</h3><span class="small muted">${rows.length} observations</span></div>${graphSVG(rows,first.low,first.high)}<div class="small muted" style="margin-top:8px">${esc(first.unit)} · Version ${first.version} · ${first.low}–${first.high} · Never mix incompatible scale definitions.</div><details style="margin-top:10px"><summary class="small">View data table</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Value</th><th>Source</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.observed_on)}</td><td>${esc(r.value)}</td><td>${esc(r.source)}</td></tr>`).join('')}</tbody></table></div></details></div>`}).join(''):'<div class="empty">Choose an athlete with observations or record a new score to see a graph.</div>'}</section><section class="card"><h3>Defined ${isAssess?'assessment':'monitoring'} factors</h3>${available.length?available.map(f=>`<div class="list-row"><div class="spacer"><h4>${esc(f.label)}</h4><p>${esc(f.group_name)} · ${f.low}–${f.high} ${esc(f.unit)} · v${f.version}</p></div>${badge(f.higher_is.replace('-',' '))}</div>`).join(''):'<div class="empty">No factors of this type have been configured.</div>'}<p class="foot-note">Custom factors are descriptive. Percentiles and Z-scores require verified instrument-specific transformations; merely naming a factor does not validate it.</p></section></div>`}
function interventionsPage(){return `${pageHead('Intervention Studio','Evidence-referenced options, selected and adapted by practitioners.',button('Assign to case','assign-intervention','btn primary'))}<div class="service-grid">${state.interventions.map((i,j)=>`<article class="service-card"><span class="service-icon">${['✺','◈','◎'][j%3]}</span><h3>${esc(i.name)}</h3><p>${esc(i.purpose)}</p><p class="small muted">${esc(i.method)}</p><div class="small muted">${esc(i.evidence_note)}</div><a class="small" href="${esc(i.reference_url)}" target="_blank" rel="noreferrer noopener">Reference / further reading ↗</a></article>`).join('')}</div><p class="foot-note">References are starting points for professional review, not a claim that each particular script has been clinically validated.</p>`}
function reportsPage(){return `${pageHead('Reports','Draft reports for professional review. No direct coach or athlete portal.',button('+ New report draft','new-report','btn primary'))}${state.athleteId?`<div class="card"><h3>Reports for selected athlete</h3>${state.reports.length?state.reports.map(r=>`<div class="note-box"><div class="between"><strong>${esc(r.title)} · ${esc(r.audience)}</strong>${badge(r.status)}</div><p class="small">${when(r.created_at)}</p><div>${esc(r.body)}</div><div class="inline-actions">${betaEnabled('report_exports')?`${button('PDF draft','export-report','btn small',`data-id="${r.id}" data-format="pdf"`)}${button('CSV draft','export-report','btn small',`data-id="${r.id}" data-format="csv"`)}`:badge('Pro · Exports locked','gold')}</div></div>`).join(''):'<div class="empty">No report drafts yet.</div>'}</div>`:'<div class="empty">Select an athlete under Athletes & Cases before writing a report.</div>'}<p class="foot-note">Reports are not automatically transmitted. Any future disclosure workflow must check consent, recipient and permitted fields.</p>`}
// Version 1.0 standalone features. Sensitive records never load through public-directory endpoints.
const betaLabel='SPPS Version 1.0 - Free';
const betaEnabled=feature=>Boolean(state.tierInfo?.features?.[feature]);
const betaLock=feature=>betaEnabled(feature)?'':badge('Unavailable','gold');
const svgLogo=()=>`<div class="beta-logo-stage" aria-hidden="true"><svg class="beta-logo" viewBox="0 0 240 240" focusable="false"><defs><linearGradient id="beta-logo-gradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#ffd476"/><stop offset="35%" stop-color="#66c9f3"/><stop offset="70%" stop-color="#4cddc1"/><stop offset="100%" stop-color="#ffa4c3"/></linearGradient><linearGradient id="beta-logo-pulse" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#ffd476"/><stop offset="45%" stop-color="#70d7f4"/><stop offset="100%" stop-color="#61edca"/></linearGradient></defs><circle cx="120" cy="120" r="93" class="logo-ring-guide" fill="none" stroke="#99d9e7" stroke-opacity=".24" stroke-width="2"/><circle cx="120" cy="120" r="93" class="logo-ring outer" fill="none" stroke="url(#beta-logo-gradient)" stroke-width="5" stroke-linecap="round" stroke-dasharray="210 32 85 257"/><circle cx="120" cy="120" r="74" class="logo-ring inner" fill="none" stroke="#ffd476" stroke-width="3" stroke-linecap="round" stroke-dasharray="9 18"/><circle cx="120" cy="120" r="61" fill="#0b2e48" fill-opacity=".36"/><path class="logo-pulse-base" d="M50 118 H74 L92 101 L110 145 L129 92 L147 118 H190" stroke="url(#beta-logo-pulse)" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path class="logo-pulse-tracer" d="M50 118 H74 L92 101 L110 145 L129 92 L147 118 H190" pathLength="100" stroke="#effffa" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><text x="120" y="182" text-anchor="middle" fill="#e8fcf7" font-size="19" font-weight="800" letter-spacing="4">SPPS</text></svg></div>`;
function programmeDialog(){openDialog('Create a practitioner programme',`<p class="small muted">Offer 4, 6, 8 or custom weeks; use an upfront price and define the services included. SPPS Version 1.0 - Free accepts enquiries, not purchases.</p><form data-form="practitioner-programme"><div class="field"><label>Programme title</label><input name="title" maxlength="120" required placeholder="4-week mental skills programme"></div><div class="field-grid"><div class="field"><label>Duration (weeks)</label><select name="duration_weeks"><option value="4">4 weeks</option><option value="6">6 weeks</option><option value="8">8 weeks</option>${Array.from({length:12},(_,i)=>i+1).filter(n=>![4,6,8].includes(n)).map(n=>`<option value="${n}">${n} weeks (custom)</option>`).join('')}</select></div><div class="field"><label>Included sessions</label><input name="sessions_included" type="number" min="1" max="50" value="4" required></div><div class="field"><label>One-time programme price (₹)</label><input name="amount_rupees" type="number" min="100" max="1000000" step="1" value="6000" required></div></div><div class="field"><label>Included services, frequency and exclusions</label><textarea name="deliverables" minlength="12" maxlength="1400" rows="4" required placeholder="4 online check-ins, a custom mental skills plan, practice exercises, one progress report; no emergency services"></textarea></div><p class="small muted">The quoted price is saved when a client enquires. The existing 70:30 paid-booking ledger does not yet process programme purchases.</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Publish programme</button></div></form>`)}
function programmeRequestDialog(id){const p=(state.publicProgrammes||[]).find(x=>x.id===id);if(!p){notify('Programme no longer available');return}openDialog('Enquire about programme',`<p><b>${esc(p.title)}</b> · ${p.duration_weeks} weeks · ₹${(p.amount_paise/100).toLocaleString('en-IN')}</p><p class="small muted">${esc(p.deliverables)}</p><form data-form="programme-request"><input type="hidden" name="programme_id" value="${p.id}"><div class="field-grid"><div class="field"><label>Contact name</label><input name="contact_name" required minlength="2" maxlength="120"></div><div class="field"><label>Contact email</label><input name="contact_email" required type="email"></div></div><div class="field"><label>Scheduling preferences (no clinical details)</label><textarea name="message" maxlength="350" rows="2"></textarea></div><label class="checkline"><input type="checkbox" name="is_minor"><span class="check-copy">This enquiry concerns a minor</span></label><label class="checkline"><input type="checkbox" name="guardian_confirmed"><span class="check-copy">I have parent/guardian authority if applicable</span></label><label class="checkline"><input type="checkbox" name="contact_consent" required><span class="check-copy">I consent to being contacted about this enquiry under the privacy notice.</span></label><input name="website" class="honeypot" tabindex="-1" autocomplete="off" aria-hidden="true"><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Send enquiry (no charge)</button></div></form>`)}
function organisationQuoteDialog(){openDialog('Annual organisation licence enquiry',`<p class="small muted">For academies, clubs, corporate groups and government sports systems with multiple practitioners. Annual usage fees are negotiated by quotation; no payment is collected here.</p><form data-form="organisation-quote"><div class="field-grid"><div class="field"><label>Organisation</label><input name="organisation_name" minlength="3" maxlength="160" required></div><div class="field"><label>Contact name</label><input name="contact_name" minlength="2" required></div><div class="field"><label>Work email</label><input name="contact_email" type="email" required></div><div class="field"><label>Psychologists / practitioner seats</label><input name="practitioner_seats" type="number" min="1" max="500" value="3" required></div><div class="field"><label>Estimated athletes</label><input name="athletes_estimate" type="number" min="0" max="100000" value="50" required></div></div><div class="field"><label>Requirements (no athlete data)</label><textarea name="requirements" maxlength="700" rows="3" placeholder="Multi-psychologist workspace, reporting, SSO, support requirements"></textarea></div><label class="checkline"><input type="checkbox" name="contact_consent" required><span class="check-copy">I permit contact about a software quotation and accept the privacy notice.</span></label><input name="website" class="honeypot" tabindex="-1" autocomplete="off" aria-hidden="true"><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Request annual quote</button></div></form>`)}
function betaBookingDialog(prid,slots){const practitioner=state.practitioners.find(p=>p.id===prid);openDialog('Request a paid consultation',`<form data-form="beta-book"><div class="field"><label>Practitioner</label><input readonly value="${esc(practitioner?.name||'Practitioner')}"></div><div class="field"><label>Available appointment</label><select name="slot_id" required>${slots.map(s=>`<option value="${s.id}">${when(s.starts_at)} · ${new Date(s.starts_at).toLocaleTimeString('en-IN',{hour:'numeric',minute:'2-digit'})} · ₹${(s.amount_paise/100).toFixed(2)}</option>`).join('')}</select></div><div class="field-grid"><div class="field"><label>Contact name</label><input name="contact_name" required maxlength="120"></div><div class="field"><label>Email</label><input name="contact_email" type="email" required></div></div><label class="checkline"><input name="is_minor" type="checkbox"><span class="check-copy">This booking is for a minor</span></label><label class="checkline"><input name="guardian_confirmed" type="checkbox"><span class="check-copy">I am authorised as the minor's parent/guardian (required when applicable)</span></label><label class="checkline"><input name="contact_consent" type="checkbox" required><span class="check-copy">I accept the <a target="_blank" rel="noopener" href="/terms">booking terms</a> and <a target="_blank" rel="noopener" href="/privacy">Privacy Policy</a> for scheduling and payment processing.</span></label><label class="checkline"><input name="video_consent" type="checkbox"><span class="check-copy">I explicitly opt in to a live video consultation. No recording or facial-emotion analysis is provided.</span></label><div class="form-actions">${button('Cancel','modal-close','btn')}<button class="btn primary" type="submit">Request booking</button></div><p class="small muted">A slot is reserved pending verified payment. Practitioner approval is required before a meeting link is issued.</p></form>`)}
function betaBookingsPage(){if(state.freeAccess)return freeBookingsPage();const bs=state.bookings||[],slots=state.slots||[];return `${pageHead('Bookings & Revenue','Consultation approvals and payment allocations for Core and Pro. Allocated does not mean transferred.',`<div class="inline-actions">${button('Refresh','refresh-data','btn')}${button('+ Appointment slot','new-slot','btn primary')}</div>`)}${!betaEnabled('bookings')?`<section class="card">${badge('Sign-in required','gold')}<p>Directory, booking approvals and video consultations are available on both Free and Pro after credential verification.</p></section>`:`<section class="card"><div class="between"><h3>Published appointment slots</h3><p class="small muted">These are your offered times, not booking confirmations.</p></div>${slots.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>When</th><th>Duration</th><th>Fee</th><th>Availability</th></tr></thead><tbody>${slots.map(s=>`<tr><td>${esc(new Date(s.starts_at).toLocaleString('en-IN',{dateStyle:'medium',timeStyle:'short'}))}</td><td>${Number(s.duration_minutes)} min</td><td>₹${(s.amount_paise/100).toFixed(2)}</td><td>${badge(s.status)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No appointment slots have been created yet.</div>'}</section><section class="card"><div class="between"><h3>Pending and approved consultations</h3>${badge('Provider payouts require configuration')}</div>${bs.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>Client</th><th>Appointment</th><th>Status</th><th>Split / payout</th><th>Actions</th></tr></thead><tbody>${bs.map(b=>`<tr><td><b>${esc(b.contact_name)}</b><div class="small muted">${esc(b.contact_email)}</div></td><td>${when(b.starts_at)}</td><td>${badge(b.status)}</td><td>${b.allocation?`₹${(b.allocation.practitioner_paise/100).toFixed(2)} practitioner / ₹${(b.allocation.platform_paise/100).toFixed(2)} platform<br><span class="small muted">${esc(b.allocation.payout_status)}</span>`:'Awaiting verified capture'}</td><td><div class="inline-actions">${b.status==='paid_pending_review'?button('Approve','approve-booking','btn small primary',`data-id="${b.id}"`):''}${b.status==='approved'?button('Video links','room-invite','btn small',`data-id="${b.id}"`)+button('Mark completed','complete-booking','btn small',`data-id="${b.id}"`):''}${b.room_id?button('Session review','session-review','btn small',`data-id="${b.room_id}"`):''}</div></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No booking requests yet.</div>'}</section><section class="card"><div class="between"><h3>Programme enquiries</h3>${button('Add programme','new-programme','btn small primary')}</div>${state.programmeEnquiries.length?state.programmeEnquiries.map(q=>`<div class="strip programme-inbox"><b>${esc(q.contact_name)}</b> · ${badge(q.status)} · ₹${(q.quoted_amount_paise/100).toLocaleString('en-IN')}<div class="small">${esc(q.contact_email)} · ${esc(q.message||'No additional scheduling request')}</div>${q.status==='new'?button('Mark contacted','programme-contacted','btn small',`data-id="${q.id}"`):''}</div>`).join(''):'<div class="empty">No programme enquiries. Clients can request a programme from your public card.</div>'}<p class="small muted">Programme enquiries do not take payment, reserve sessions or create clinical records yet.</p></section><p class="foot-note">Financial transfers are never represented as completed until confirmed by the payment provider.</p>`}`}
function betaAssessmentPage(){const runs=state.offlineAssessments||[];const grouped=new Map();for(const r of runs)for(const s of r.scores){const k=`${r.instrument_name} · ${s.parent_factor} / ${s.sub_factor} (${s.unit}; ${r.instrument_version})`;if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push({observed_on:r.observed_on,value:s.value})}
return `${pageHead('Assessments & Longitudinal Trends','Formal offline assessments, nested factors and practitioner-entered score histories.',`<div class="flex">${button('+ Offline assessment','offline-assessment','btn primary')}${button('+ Custom score factor','new-factor','btn')}${button('Refresh records','refresh-clinical','btn')}</div>`)}<section class="card">${clinicalAthletePicker()}<p class="small muted">Instrument scoring versions and units remain separated. Raw scores, percentiles, Z-scores, T-scores and sten values are not converted automatically or treated as diagnostic thresholds.</p>${state.athleteId?`<h3>Offline assessment history</h3>${runs.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Assessment / version</th><th>Sub-factor</th><th>Score</th></tr></thead><tbody>${runs.flatMap(r=>r.scores.map(s=>`<tr><td>${esc(r.observed_on)}</td><td>${esc(r.instrument_name)} <span class="small muted">${esc(r.instrument_version)}</span></td><td>${esc(s.parent_factor)} / ${esc(s.sub_factor)}</td><td>${esc(s.value)} ${esc(s.unit)}</td></tr>`)).join('')}</tbody></table></div>`:'<div class="empty">No offline assessment scores recorded.</div>'}${[...grouped].map(([key,rows])=>`<div class="score-trend"><h4>${esc(key)}</h4>${betaChart(rows.map(r=>({date:r.observed_on,value:r.value})),state.chartType)}</div>`).join('')}`:'<div class="empty">Create or select an athlete to record assessment results.</div>'}</section><div class="card" style="margin-top:16px"><h3>Custom-factor assessment observations</h3>${state.observations.filter(o=>o.kind==='assessment').length?state.observations.filter(o=>o.kind==='assessment').map(o=>`<div class="strip">${esc(o.observed_on)} · ${esc(o.label)}: ${esc(o.value)} ${esc(o.unit)}</div>`).join(''):'<p class="small muted">No legacy factor observations.</p>'}</div>`}
function betaChart(rows,type='line'){if(!rows?.length)return '<div class="empty">No values available.</div>';const values=rows.filter(x=>Number.isFinite(Number(x.value))).sort((a,b)=>String(a.date).localeCompare(String(b.date))).slice(-60);if(!values.length)return '<div class="empty">No numeric observations.</div>';const w=670,h=190,left=40,right=16,bottom=35,top=20,innerW=w-left-right,innerH=h-top-bottom;let lo=Math.min(0,...values.map(x=>Number(x.value))),hi=Math.max(1,...values.map(x=>Number(x.value)));if(hi===lo)hi=lo+1;const X=i=>left+innerW*(values.length===1?.5:i/(values.length-1)),Y=v=>top+innerH-(Number(v)-lo)/(hi-lo)*innerH;const dots=values.map((v,i)=>({x:X(i),y:Y(v.value),value:v.value,label:v.date}));const pts=dots.map(d=>`${d.x.toFixed(1)},${d.y.toFixed(1)}`).join(' ');const shape=type==='bar'?dots.map(d=>`<rect x="${d.x-7}" y="${d.y}" width="14" height="${top+innerH-d.y}" fill="var(--ocean)"/>`).join(''):type==='scatter'?dots.map(d=>`<circle cx="${d.x}" cy="${d.y}" r="6" fill="var(--pink)"/>`).join(''):type==='area'||type==='multi-area'?`<polygon points="${left},${top+innerH} ${pts} ${dots.at(-1).x},${top+innerH}" fill="var(--teal)" fill-opacity=".16"/><polyline points="${pts}" fill="none" stroke="var(--teal)" stroke-width="3"/>`:type==='radar'?(()=>{const n=values.length;const max=hi||1;const rad=Math.PI*2/n;const cx=w/2,cy=h/2,r=70;const points=values.map((v,i)=>{const radius=(Number(v.value)-lo)/(hi-lo)*r;return `${cx+radius*Math.sin(i*rad)},${cy-radius*Math.cos(i*rad)}`}).join(' ');return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)"/><polygon points="${points}" fill="var(--teal)" fill-opacity=".2" stroke="var(--teal)" stroke-width="2"/>`})():`<polyline points="${pts}" fill="none" stroke="var(--teal)" stroke-width="3"/>${dots.map(d=>`<circle cx="${d.x}" cy="${d.y}" r="4" fill="var(--gold)"/>`).join('')}`;return `<div class="chart-area"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(type)} chart showing ${values.length} recorded measurements"><line x1="${left}" y1="${top+innerH}" x2="${w-right}" y2="${top+innerH}" stroke="var(--line)"/><text x="4" y="${top+8}" fill="var(--muted)">${hi.toFixed(1)}</text><text x="4" y="${top+innerH}" fill="var(--muted)">${lo.toFixed(1)}</text>${shape}<text x="${left}" y="${h-8}">${esc(values[0].date||'')}</text><text x="${w-130}" y="${h-8}">${esc(values.at(-1).date||'')}</text></svg></div><details><summary class="small">Accessible data table</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Value</th></tr></thead><tbody>${values.map(v=>`<tr><td>${esc(v.date)}</td><td>${esc(v.value)}</td></tr>`).join('')}</tbody></table></div></details>`}
function betaDualArea(rows){
 if(!rows.length)return '<div class="empty">No concurrent index observations to compare.</div>';
 const W=680,H=220,L=54,R=55,T=15,B=35,w=W-L-R,h=H-T-B;
 const x=i=>L+(rows.length===1?w/2:i*w/(rows.length-1));const y=v=>T+h-(Number(v)/100)*h;
 const series=[{key:'load_index',name:'Load Index',color:'var(--pink)'},{key:'recovery_index',name:'Recovery Index',color:'var(--teal)'}];
 let shapes='';for(const ser of series){let segment=[];const emit=()=>{if(!segment.length)return;const p=segment.map(d=>`${d.x},${d.y}`).join(' ');shapes+=`<polygon points="${segment[0].x},${T+h} ${p} ${segment.at(-1).x},${T+h}" fill="${ser.color}" fill-opacity=".14"/><polyline points="${p}" fill="none" stroke="${ser.color}" stroke-width="3"/>`;segment=[]};rows.forEach((o,i)=>{const v=o[ser.key];if(v===null||v===undefined)emit();else segment.push({x:x(i),y:y(v)})});emit()}
 return `<div class="chart-area"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Two-axis area chart of Load and Recovery indices, both 0 to 100"><line x1="${L}" x2="${L}" y1="${T}" y2="${T+h}" stroke="var(--muted)"/><line x1="${W-R}" x2="${W-R}" y1="${T}" y2="${T+h}" stroke="var(--muted)"/><text x="5" y="${T+10}">100</text><text x="5" y="${T+h}">0</text><text x="${W-42}" y="${T+10}">100</text><text x="${W-30}" y="${T+h}">0</text>${shapes}<text x="${L}" y="${H-8}">${esc(rows[0].date)}</text><text x="${W-145}" y="${H-8}">${esc(rows.at(-1).date)}</text></svg></div><div class="legend"><span style="color:var(--pink)">● Load (left)</span><span style="color:var(--teal)">● Recovery (right)</span></div><p class="small muted">Two labelled axes, each 0–100. Missing data are left unplotted; the indices are not validated clinical measures.</p>`
}
function betaAnalyticsPage(){const rows=state.indices?.series||[];return `${pageHead('Load & Recovery Analytics','Descriptive index trends from available, practitioner-defined monitoring inputs.',`<select aria-label="Chart type" data-action="chart-switch" class="input" style="max-width:180px">${['line','bar','area','scatter','radar','multi-area'].map(x=>`<option value="${x}" ${x===state.chartType?'selected':''}>${esc(x)}</option>`).join('')}</select>`)}${state.chartType==='multi-area'?`<section class="card" style="margin-bottom:16px"><h3>Load and Recovery · Dual-Axis Area Comparison</h3>${betaDualArea(rows)}</section>`:''}<div class="content-grid"><section class="card"><h3>Load Index</h3>${betaChart(rows.filter(x=>x.load_index!==null).map(x=>({date:x.date,value:x.load_index})),state.chartType)}</section><section class="card"><h3>Recovery Index</h3>${betaChart(rows.filter(x=>x.recovery_index!==null).map(x=>({date:x.date,value:x.recovery_index})),state.chartType)}</section></div><p class="foot-note">${esc(state.indices?.method||'Indices are descriptive and not clinically validated.')} Empty or missing inputs are not imputed. These numbers must not drive automatic safety decisions.</p>`}
function betaInjuryPage(){const episodes=state.injuries||[];return `${pageHead('Injury Psychology','Psychological rehabilitation records linked to the selected athlete and case.',button('+ Injury episode','new-injury','btn primary'))}<div class="content-grid">${episodes.length?episodes.map(ep=>`<section class="card"><div class="between"><div><h3>${esc(ep.injury_label)}</h3><p class="small muted">${esc(ep.stage)} · Started ${esc(ep.start_date)}</p></div>${button('+ Rehab check-in','injury-checkin','btn small',`data-id="${ep.id}"`)}</div><div class="factor-grid">${['pain','fear_of_reinjury','confidence','coping'].map(key=>`<div><h4>${esc(key.replaceAll('_',' '))}</h4>${betaChart(ep.checkins.map(x=>({date:x.observed_on,value:x[key]})),state.chartType)}</div>`).join('')}</div>${ep.checkins.map(x=>`<div class="strip">${esc(x.observed_on)} · ${esc(x.note)}</div>`).join('')}</section>`).join(''):'<section class="card"><div class="empty">Select an athlete case and create an episode to track rehabilitation support. Psychological readiness is not medical clearance.</div></section>'}</div>`}
function betaSettingsPage(){if(state.freeAccess)return freeSettingsPage();return `${pageHead('Account, Privacy & Plans','Manage practitioner profile and access tier.') }<div class="content-grid"><section class="card"><h3>Subscription</h3><p>Current plan: ${badge((state.user?.tier||'core').toUpperCase(),'teal')}</p>${[['Core · ₹199/month','All core functions including public listing, check-ins, assessments, injury psychology, scheduling, video and private drafts'],['Pro · ₹499/month','All Core functions plus PDF/CSV report exports'],['Proposed annual prepay · ₹1,990 Core / ₹4,990 Pro','Illustrative 12-month price with two months free. not available for payment or activation in SPPS Version 1.0 - Free. No lifetime access is promised.']].map(x=>`<div class="strip"><b>${x[0]}</b> — ${x[1]}</div>`).join('')}<p class="foot-note">Paid plan activation is required. Live recurring billing requires configured Razorpay plans and signed webhooks.</p></section><section class="card"><h3>Profile for credential review</h3><p class="small muted">Edits revoke public approval until independently verified.</p>${button('Edit qualifications','profile-edit','btn')}</section><section class="card"><div class="between"><h3>My published programmes</h3>${button('+ Create programme','new-programme','btn small primary')}</div><p class="small muted">Set a fixed one-time programme price and spell out what you deliver. SPPS Version 1.0 - Free supports enquiries only, not a programme checkout.</p>${state.ownProgrammes.map(pkg=>`<div class="strip"><b>${esc(pkg.title)}</b> · ${pkg.duration_weeks} weeks · ${pkg.sessions_included} sessions · ₹${(pkg.amount_paise/100).toLocaleString('en-IN')}<p class="small">${esc(pkg.deliverables)}</p>${badge(pkg.published?'Published':'Hidden')} ${button(pkg.published?'Hide':'Publish','toggle-programme','btn small',`data-id="${pkg.id}" data-status="${pkg.published?'hidden':'published'}"`)}</div>`).join('')||'<p class="small muted">No practitioner-created packages yet.</p>'}</section>${state.user?.is_admin?`<section class="card"><div class="between"><h3>Organisation quotation inbox ${badge(String(state.organisationQuotes.length),'teal')}</h3>${button('Refresh enquiries','refresh-organisation-quotes','btn small')}</div><p class="small muted">Annual multi-practitioner licence requests; pricing is negotiated. No automatic invoices. New requests refresh whenever this page opens.</p>${state.organisationQuotes.map(q=>`<div class="strip"><b>${esc(q.organisation_name)}</b> · ${q.practitioner_seats} practitioner seats · ${q.athletes_estimate} estimated athletes<div class="small">${esc(q.contact_name)} · ${esc(q.contact_email)} · ${esc(q.requirements)}</div></div>`).join('')||'<p class="small muted">No institutional quotation requests yet.</p>'}</section>`:''}<section class="card"><h3>Privacy & terms</h3><p><a href="/privacy" target="_blank">Privacy Policy</a> · <a href="/terms" target="_blank">Terms of Use</a> · <a href="/policies" target="_blank">Policies</a> · <a href="/guide" target="_blank">How to use SPPS</a></p></section></div>`}
function betaOfflineDialog(){if(state.athleteLoading){notify('Athlete selection is loading. Wait before recording.');return}if(!state.athleteId||!state.caseId){notify('Select an athlete and case first');return}openDialog('Record offline assessment',`<form data-form="offline-assessment" data-athlete-id="${state.athleteId}" data-case-id="${state.caseId}"><div class="field-grid"><div class="field"><label>Parent assessment</label><input required name="instrument_name" placeholder="Assessment name"></div><div class="field"><label>Edition/version</label><input name="instrument_version" value="offline custom"></div><div class="field"><label>Assessment date</label><input type="date" name="observed_on" required value="${fdate()}"></div><div class="field"><label>Parent factor</label><input name="parent_factor" required placeholder="e.g. Anxiety"></div><div class="field"><label>Sub-factors</label><textarea name="scores" rows="4" required placeholder="Cognitive anxiety | Raw Score | 22&#10;Somatic anxiety | Percentile | 75"></textarea></div><div class="field"><label>Notes</label><input name="note" placeholder="Context (optional)"></div></div><p class="small muted">One sub-factor per line: Name | Unit | Value. Units: Raw Score, Percentile, Z-Score, T-Score, Sten.</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Save assessment</button></div></form>`)}
function betaInjuryDialog(){if(!state.caseId){notify('Select an athlete case first');return}openDialog('Start injury psychology episode',`<form data-form="injury-episode"><div class="field"><label>Injury label (avoid identifiers)</label><input name="injury_label" required></div><div class="field"><label>Start date</label><input name="start_date" type="date" required value="${fdate()}"></div><div class="field"><label>Rehab stage</label><select name="stage">${['early rehabilitation','mid rehabilitation','late rehabilitation','return-to-training','return-to-competition'].map(x=>`<option>${x}</option>`).join('')}</select></div><div class="form-actions"><button type="submit" class="btn primary">Create episode</button></div></form>`)}
function betaInjuryCheckDialog(id){openDialog('Psychological rehabilitation check-in',`<form data-form="injury-checkin" data-id="${id}"><div class="field"><label>Date</label><input type="date" name="observed_on" value="${fdate()}"></div><div class="field-grid">${['pain','fear_of_reinjury','confidence','coping'].map(x=>`<div class="field"><label>${esc(x.replaceAll('_',' '))} (0–10)</label><input type="number" min="0" max="10" step="0.1" required name="${x}" value="5"></div>`).join('')}</div><div class="field"><label>Observations</label><textarea name="note" rows="3"></textarea></div><div class="form-actions"><button class="btn primary" type="submit">Save rehab check-in</button></div></form>`)}
async function profilePhotoBase64(file){if(file.size>5*1024*1024)throw Error('Select an image below 5 MB');return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('Invalid image'));reader.onload=()=>{const image=new Image();image.onload=()=>{try{const w=image.width,h=image.height;if(!w||!h)throw Error('Invalid photo');const scale=Math.min(1,512/Math.max(w,h));const canvas=document.createElement('canvas');canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);resolve(canvas.toDataURL('image/webp',0.78))}catch(e){reject(e)}};image.onerror=()=>reject(Error('Invalid image'));image.src=reader.result};reader.readAsDataURL(file)})}
function betaSlotDialog(){openDialog('Publish general availability',`<p class="small muted">You are not assigning this slot to an athlete. Anyone browsing your public practitioner profile may request it. After a client books, their details appear in Bookings & Revenue.</p><form data-form="appointment-slot"><div class="field-grid"><div class="field"><label>Date and time</label><input name="starts_at" type="datetime-local" required></div><div class="field"><label>Duration (minutes)</label><input name="duration_minutes" type="number" min="20" max="120" value="50" required></div><div class="field"><label>Consultation fee (₹)</label><input name="amount_rupees" type="number" min="100" step="1" value="1500" required></div></div><div class="form-actions"><button class="btn primary" type="submit">Publish slot</button></div></form>`)}
function betaProfileDialog(){const p=state.privateProfile||{};openDialog('Update practitioner profile',`<form data-form="profile-edit"><div class="field"><label>Qualifications</label><textarea name="qualifications" rows="3" required>${esc(p.qualifications||'')}</textarea></div><div class="field"><label>Years of experience</label><input type="number" min="0" max="70" name="experience_years" required value="${Number(p.experience_years||0)}"></div><div class="field"><label>Achievements</label><textarea name="achievements" rows="3">${esc(p.achievements||'')}</textarea></div><div class="field"><label>Specialisms</label><input name="specialisms" value="${esc(p.specialisms||'')}"></div><div class="form-actions"><button class="btn primary" type="submit">Save for re-verification</button></div></form>`)}
function lifecycleCard(){
 const l=state.lifecycle;if(!l||!l.enabled)return '';
 const since=l.deactivated_at?new Date(l.deactivated_at).toLocaleDateString():'';
 return `<section class="card" id="account-lifecycle"><h3>Your account</h3>${l.deactivated?`<p>Your public listing has been offline since ${esc(since)}. Visitors cannot find you or request appointments with you. Your workspace and records still work.</p><p>${button('Bring my listing back','account-reactivate','btn primary')}</p>`:`<p>Your listing is visible in Find a Sport Psychologist. You can take it offline at any time and bring it back without a new review.</p><p>${button('Take my listing offline','account-deactivate','btn')}</p>`}<h4>Export my data</h4><p class="small muted">One file with your profile, athletes, cases, notes and other records. It contains confidential client information, so store it securely. You will be asked to confirm your password.</p><p>${button('Export my data','account-export','btn')}</p><p class="small muted">Closing or permanently deleting an account is not available yet. Contact the platform administrator if you need that.</p></section>`;
}
function accountDeactivateDialog(){openDialog('Take your listing offline',`<form data-form="account-deactivate"><p>Visitors will no longer find you in Find a Sport Psychologist or be able to request appointments with you. You keep full access to your workspace and records, and you can bring your listing back at any time without a new review.</p><div class="form-actions"><button class="btn primary" type="submit">Take my listing offline</button></div></form>`)}
function accountExportDialog(){openDialog('Export my data',`<form data-form="account-export"><p>The file contains confidential client information that you hold as a practitioner. You are responsible for storing and sharing it securely and lawfully.</p><div class="field"><label>Confirm your password</label><input type="password" name="password" required autocomplete="current-password"></div><div class="form-actions"><button class="btn primary" type="submit">Download my data</button></div></form>`)}
async function accountExportDownload(password){
 let body={password};
 if(state.lifecycle?.reauth==='supabase'){
  const sb=await requireSupabase();
  const {data,error}=await sb.auth.signInWithPassword({email:state.lifecycle.email,password});
  if(error||!data?.session)throw Error('Password is incorrect');
  body={supabase_access_token:data.session.access_token};
 }
 const res=await fetch('/api/account/export',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(body)});
 if(!res.ok){let msg='Export failed';try{const d=await res.json();if(typeof d.detail==='string')msg=d.detail}catch{}throw Error(`${msg} (HTTP ${res.status})`)}
 const blob=await res.blob();const href=URL.createObjectURL(blob);const link=document.createElement('a');link.href=href;link.download=`spps-export-${new Date().toISOString().slice(0,10)}.json`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),2000);
}
async function betaLoadAthlete(){const selectedId=state.athleteId,sequence=athleteLoadSequence,writeRevision=clinicalWriteRevision;if(!selectedId){Object.assign(state,{offlineAssessments:[],indices:{series:[]},injuries:[]});return}const [a,b,c]=await Promise.all([api(`/api/athletes/${selectedId}/assessments/offline`),api(`/api/athletes/${selectedId}/indices`),api(`/api/athletes/${selectedId}/injury`)]);if(sequence!==athleteLoadSequence||state.athleteId!==selectedId||writeRevision!==clinicalWriteRevision)return;Object.assign(state,{offlineAssessments:a,indices:b,injuries:c})}
async function refreshOrganisationQuotes(){state.organisationQuotesUnavailable=false;try{state.organisationQuotes=state.user?.is_admin?await api('/api/admin/organisation-quotes'):[]}catch(err){if(!err.unavailable)throw err;state.organisationQuotes=[];state.organisationQuotesUnavailable=true}return state.organisationQuotes}
async function betaLoadAll(){try{state.tierInfo=await api('/api/account/tier');state.user.tier=state.tierInfo.tier;state.bookings=betaEnabled('bookings')?await api('/api/bookings'):[];state.slots=betaEnabled('bookings')?await api('/api/slots'):[];if(state.portalEnabled)state.appointments=await api('/api/booking-requests');state.privateProfile=betaEnabled('directory')?await api('/api/practitioner/profile'):{};state.ownProgrammes=await api('/api/practitioner/programmes');state.programmeEnquiries=await api('/api/practitioner/programme-enquiries');await refreshOrganisationQuotes();await betaLoadAthlete()}catch(err){if(!err.unavailable)notify('Some modules are unavailable: '+err.message)}}


async function betaCheckout(bookingId){
 const order=await api(`/api/public/bookings/${bookingId}/payment-order`,{method:'POST'});
 if(!window.Razorpay){await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='https://checkout.razorpay.com/v1/checkout.js';script.onload=resolve;script.onerror=()=>reject(Error('Payment checkout unavailable'));document.head.appendChild(script)})}
 const checkout=new window.Razorpay({key:order.key_id,order_id:order.order_id,amount:order.amount_paise,currency:'INR',name:'Sport Psychology Practitioner Suite',description:'Consultation booking',handler:async res=>{try{const verified=await api(`/api/public/bookings/${bookingId}/verify-payment`,{method:'POST',body:res});closeDialog();notify(`Payment verified. Practitioner review pending; allocation status ${verified.allocation_status}.`)}catch(err){notify(`Verification pending: ${err.message}`)}}});checkout.open();
}


// SPPS Version 1.0 SPA navigation: store screen identity in browser history so
// both the in-app Back control and WebView history restore previous selections.
const SPPS_NAV_KEY='sppsNavigationV1';
let restoringNavigation=false;
function navigationSnapshot(){
 return {mode:state.mode,view:state.view,athleteId:state.athleteId??null,caseId:state.caseId??null};
}
function syncNavigationEntry(){
 if(restoringNavigation)return;
 const previous=history.state&&typeof history.state==='object'?history.state:{};
 const depth=Number.isInteger(previous[SPPS_NAV_KEY]?.depth)?previous[SPPS_NAV_KEY].depth:0;
 history.replaceState({...previous,[SPPS_NAV_KEY]:{...navigationSnapshot(),depth}},'',location.href);
}
function pushNavigation(next,path='/studio'){
 if(restoringNavigation)return;
 const current=navigationSnapshot(),target={...current,...next};
 if(current.mode===target.mode&&current.view===target.view&&current.athleteId===target.athleteId&&current.caseId===target.caseId)return;
 syncNavigationEntry();
 const depth=(history.state?.[SPPS_NAV_KEY]?.depth||0)+1;
 history.pushState({[SPPS_NAV_KEY]:{...target,depth}},'',path);
}
function navigateSection(mode,path){
 if(state.mode!==mode)pushNavigation({mode,view:mode==='studio'?'overview':state.view},path);
 state.mode=mode;
}
function workspaceNavigation(next){
 if(state.mode==='studio'&&state.user)pushNavigation({mode:'studio',...next},'/studio');
}
function backButtonHTML(){
 return '<div class="spps-back-row"><button type="button" class="btn spps-back-btn" data-action="app-back" aria-label="Go back to previous SPPS screen">← Back</button></div>';
}
async function restoreNavigationScreen(event){
 const saved=event.state?.[SPPS_NAV_KEY];
 restoringNavigation=true;
 try{
  const mode=saved?.mode||(location.pathname==='/register'?'register':location.pathname==='/studio'?'studio':location.pathname==='/services'?'public':'directory');
  state.mode=mode;
  if(mode==='studio'&&state.user){
   state.view=saved?.view||'overview';
   const athleteId=Number(saved?.athleteId)||null,caseId=Number(saved?.caseId)||null;
   if(athleteId&&state.athleteId!==athleteId)await loadAthlete(athleteId);
   else if(!athleteId&&state.athleteId!==null){
    athleteLoadSequence++;
    Object.assign(state,{athleteId:null,athleteLoading:false,athleteDetail:null,caseId:null,caseDetail:null,observations:[],reports:[],offlineAssessments:[]});
   }
   if(athleteId&&state.caseId!==caseId){state.caseId=caseId;await refreshCase();}
  }
  closeDialog();render();window.scrollTo({top:0,behavior:'instant'});
 }catch(err){render();notify('Unable to restore the previous screen: '+err.message)}
 finally{restoringNavigation=false;syncNavigationEntry()}
}

function studioPage(){let inner='';switch(state.view){case 'admin':inner=adminApprovalPage();break;case 'overview':inner=dashboardPage();break;case 'bookings':inner=betaBookingsPage();break;case 'analytics':inner=betaAnalyticsPage();break;case 'injury':inner=betaInjuryPage();break;case 'settings':inner=betaSettingsPage();break;case 'athletes':inner=athletesPage();break;case 'assessments':inner=betaAssessmentPage();break;case 'monitoring':inner=factorPage('monitoring');break;case 'interventions':inner=interventionsPage();break;case 'reports':inner=reportsPage();break;default:inner=(window.SPPSv2&&SPPSv2.page(state.view))||dashboardPage()}return `${header()}<div class="app-shell">${sidebar()}<main class="app-main">${backButtonHTML()}${ownerDashboardSwitcher()}${inner}</main></div>`}
function syncAndroidSystemBars(){
 const bars=window.Capacitor?.Plugins?.SystemBars;
 if(!bars?.setStyle)return;
 Promise.resolve(bars.setStyle({style:state.theme==='dark'?'DARK':'LIGHT'})).catch(()=>{});
}
function render(){syncNavigationEntry();app.classList.toggle('spps-login-active',state.mode==='studio'&&!state.user);if(integrationDevelopment && state.mode==='studio')state.mode='studio';app.innerHTML=state.mode==='directory'?directoryPage():state.mode==='public'?publicPage():state.mode==='register'?registrationPage():state.user?studioPage():loginPage();if(state.mode!=='directory'&&!(state.mode==='studio'&&state.user)){app.querySelector('main')?.insertAdjacentHTML('beforebegin',`<div class="spps-outer-back${state.mode==='studio'&&!state.user?' spps-studio-login-back':''}">${backButtonHTML()}</div>`)}if(integrationDevelopment && state.mode==='studio'){const clinical=Boolean(state.clinicalParity);const allowedViews=clinical?['overview','athletes','assessments','monitoring','analytics','injury','interventions','reports']:['overview','athletes','assessments','monitoring','analytics'];const hiddenActions=clinical?['directory','public','organisation-quote','registration']:['directory','public','organisation-quote','registration','new-note','assign-intervention','new-report'];for(const el of app.querySelectorAll('[data-action]')){const a=el.dataset.action;if(hiddenActions.includes(a)||(a==='view'&&!allowedViews.includes(el.dataset.view)))el.hidden=true;}const notice=document.createElement('p');notice.className='shell small';notice.textContent=clinical?'Local clinical workspace · Public directory, bookings, payments and video remain locked.':'Local development workspace · Changes sync when you refresh or return to the app.';app.prepend(notice);}syncAndroidSystemBars()}
function openDialog(title,formHTML,caption=''){modal.innerHTML=`<div class="dialog-backdrop" data-action="modal-close-backdrop"><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="modal-heading"><div class="between"><h2 id="modal-heading">${esc(title)}</h2>${button('✕','modal-close','btn small')}</div>${caption?`<p>${caption}</p>`:''}${formHTML}</div></div>`;modal.querySelector('input,select,textarea,button')?.focus()}
function closeDialog(){modal.innerHTML=''}
// Stage 13D: independent credential verification and directory publication (administrators only).
async function practitionerReviewsDialog(){const rows=await api('/api/admin/practitioner-reviews');let lic=null;if(state.portalEnabled){try{lic=await api('/api/admin/licences')}catch(_){lic=null}}const licHtml=lic?`<section class="card" data-section="licences"><h3 style="margin-top:0">Institutional licences</h3><p class="muted small">A seat counts as membership only. It never verifies credentials or publishes a listing.</p>${lic.map(l=>`<div class="card" data-licence="${l.id}"><div class="between"><strong>${esc(l.organisation_name)}</strong>${badge(l.status,l.status==='ACTIVE'?'good':'')}</div><p class="small">${l.assigned.length}/${l.seats} seats · until ${esc(when(l.valid_until))}</p><p class="small">${l.assigned.map(pid=>`${esc((rows.find(r=>r.practitioner_id===pid)||{}).name||('#'+pid))} ${button('Revoke','licence-revoke','btn small',`data-id="${l.id}" data-pid="${pid}"`)}`).join(' · ')||'No seats assigned'}</p>${l.status==='ACTIVE'?`<form data-form="licence-seat" data-id="${l.id}" class="inline-actions"><select name="practitioner_id">${rows.filter(r=>!l.assigned.includes(r.practitioner_id)).map(r=>`<option value="${r.practitioner_id}">${esc(r.name)}</option>`).join('')}</select><button class="btn small" type="submit">Assign seat</button></form>`:''}</div>`).join('')}<form data-form="licence-new" class="field-grid"><div class="field"><label>Organisation<input name="organisation_name" required minlength="2" maxlength="160"></label></div><div class="field"><label>Seats<input name="seats" type="number" min="1" max="500" value="5" required></label></div><button class="btn small primary" type="submit">Create licence</button></form></section>`:'';const d=(id,gate,decision,label,cls='btn small')=>button(label,'practitioner-decision',cls,`data-id="${id}" data-gate="${gate}" data-decision="${decision}"`);const body=rows.map(r=>`<tr data-review="${r.practitioner_id}"><td>${esc(r.name)}<br><span class="muted small">${esc(r.qualifications||'No qualifications submitted')}</span></td><td>${badge(r.credential_status.replace('_',' '),r.credential_status==='verified'?'good':'')}</td><td>${badge(r.membership_active?'active':'inactive',r.membership_active?'good':'')}</td><td>${badge(r.publication_status.replace('_',' '),r.publication_status==='approved'?'good':'')}${r.listed?'<br><span class="small">Listed</span>':''}</td><td>${r.self?'<span class="muted small">Your own profile — another administrator must review</span>':`${r.credential_status!=='verified'?d(r.practitioner_id,'credentials','verify','Verify credentials','btn small primary')+d(r.practitioner_id,'credentials','reject','Reject'):d(r.practitioner_id,'credentials','suspend','Suspend credentials')}${r.publication_status!=='approved'?(r.credential_status==='verified'&&r.membership_active?d(r.practitioner_id,'publication','approve','Approve listing','btn small primary'):''):d(r.practitioner_id,'publication','hide','Hide listing')}`}</td></tr>`).join('');openDialog('Practitioner reviews',`<div class="table-wrap"><table class="data-table"><thead><tr><th>Practitioner</th><th>Credentials</th><th>Membership</th><th>Directory</th><th>Decision</th></tr></thead><tbody>${body}</tbody></table></div>${licHtml}`,'A profile is listed only when credentials are independently verified, the membership is active and an administrator approves publication. Payment alone never publishes a profile.')}
// Stage 13C: practitioner-authored programmes, athlete self-reports and released summaries.
const PROGRAMME_NEXT={DRAFT:[['ASSIGNED','Assign to athlete']],ASSIGNED:[],ACCEPTED:[['PAUSED','Pause'],['COMPLETED','Mark complete']],PAUSED:[['ACCEPTED','Resume'],['COMPLETED','Mark complete']]};
async function participationDialog(){const aid=state.athleteId;if(!aid)return;const d=await api(`/api/athletes/${aid}/participation`);if(!d.active_link){openDialog('Programme & sharing','<p class="muted">This athlete has no active portal link yet. Use <strong>Portal access</strong> to issue an invitation code first.</p>');return}
const progs=d.programmes.map(p=>{const tasks=p.tasks.map(t=>{const subs=t.submissions.slice(0,5).map(s=>`<li data-submission="${s.id}">${s.completed?'✔ completed':'✖ not completed'}${s.self_rating!==null?` · self-rating ${s.self_rating}/10`:''} · ${esc(when(s.submitted_at))} · <span class="muted small">${s.source==='GUARDIAN_PROXY'?'guardian on behalf':'athlete self-report'}</span>${s.reflection?`<br><span class="small">“${esc(s.reflection)}”</span>`:''}${s.reviews.map(r=>`<br><span class="small muted">Your feedback: ${esc(r.feedback)}</span>`).join('')}<form data-form="submission-review" data-id="${s.id}" class="inline-actions" style="margin:4px 0"><input name="feedback" maxlength="1000" placeholder="Feedback to athlete" required minlength="2"><button class="btn small" type="submit">Send</button></form></li>`).join('');return `<div class="card" data-task="${t.id}"><div class="between"><strong>${esc(t.title)}</strong><span>${badge(t.frequency)} ${badge(t.status,t.status==='ACTIVE'?'good':'')}</span></div>${t.instructions?`<p class="small">${esc(t.instructions)}</p>`:''}${t.status==='ACTIVE'&&!['COMPLETED','DISCONTINUED','DECLINED'].includes(p.status)?button('Discontinue','task-discontinue','btn small',`data-id="${t.id}"`):''}${subs?`<ul>${subs}</ul>`:'<p class="muted small">No submissions yet.</p>'}</div>`}).join('');
const moves=(PROGRAMME_NEXT[p.status]||[]).map(([s,l])=>button(l,'programme-status','btn small primary',`data-id="${p.id}" data-status="${s}"`)).join('')+(['DRAFT','ASSIGNED','ACCEPTED','PAUSED'].includes(p.status)?button('Discontinue','programme-status','btn small',`data-id="${p.id}" data-status="DISCONTINUED"`):'');
const addTask=['DRAFT','ASSIGNED','ACCEPTED','PAUSED'].includes(p.status)?`<form data-form="task-new" data-id="${p.id}" class="field-grid" style="margin-top:8px"><div class="field"><label>Task<input name="title" required minlength="3" maxlength="140"></label></div><div class="field"><label>Frequency<select name="frequency"><option value="daily">Daily</option><option value="weekly" selected>Weekly</option><option value="once">Once</option><option value="as_needed">As needed</option></select></label></div><div class="field"><label>Instructions<input name="instructions" maxlength="2000"></label></div><button class="btn small" type="submit">Add task</button></form>`:'';
return `<section class="card" data-programme="${p.id}"><div class="between"><div><span class="eyebrow">Programme · ${esc(p.status.toLowerCase())}</span><h3 style="margin:4px 0">${esc(p.title)}</h3>${p.goal?`<p class="small">${esc(p.goal)}</p>`:''}${p.response_reason?`<p class="small muted">Athlete note: ${esc(p.response_reason)}</p>`:''}</div><div class="inline-actions">${moves}</div></div>${tasks||'<p class="muted small">Add at least one task before assigning.</p>'}${addTask}</section>`}).join('');
const sums=d.summaries.map(m=>`<li data-summary="${m.id}">${badge(m.kind.replace('_',' '))} <strong>${esc(m.title)}</strong> · ${esc(when(m.released_at))}${m.withdrawn_at?' · <span class="muted">withdrawn</span>':' '+button('Withdraw','summary-withdraw','btn small',`data-id="${m.id}"`)}</li>`).join('');
openDialog('Programme & sharing',`${progs||'<p class="muted">No programmes yet.</p>'}<form data-form="programme-new" class="card"><h3 style="margin-top:0">New draft programme</h3><div class="field"><label>Title<input name="title" required minlength="3" maxlength="140"></label></div><div class="field"><label>Goal (visible to the athlete once assigned)<input name="goal" maxlength="1000"></label></div><button class="btn primary" type="submit">Create draft</button></form><section class="card"><h3 style="margin-top:0">Released summaries</h3><p class="muted small">Only text you write here is shared with the portal. Case notes, draft reports and assessment records are never shared.</p>${sums?`<ul>${sums}</ul>`:''}<form data-form="summary-release"><div class="field-grid"><div class="field"><label>Type<select name="kind"><option value="progress_summary">Progress summary</option><option value="assessment_summary">Assessment summary</option><option value="session_summary">Session summary</option></select></label></div><div class="field"><label>Title<input name="title" required minlength="3" maxlength="140"></label></div></div><div class="field"><label>Summary text<textarea name="body" required minlength="5" maxlength="2000" rows="3"></textarea></label></div><button class="btn small" type="submit">Release to portal</button></form></section>`,'Athlete self-reports are shown separately from your own observations and are never merged into clinical records.')}
// Stage 13B: approval-first client bookings from linked athletes/guardians.
const BOOKING_LABEL={PENDING_APPROVAL:'Awaiting your approval',APPROVED_AWAITING_PAYMENT:'Approved · awaiting payment',CONFIRMED:'Confirmed',DECLINED:'Declined',RESCHEDULE_PROPOSED:'New time proposed',EXPIRED:'Expired',CANCELLED:'Cancelled',REFUND_PENDING:'Refund pending',REFUNDED:'Refunded',COMPLETED:'Completed'};
const rupees=p=>`₹${(Number(p||0)/100).toLocaleString('en-IN',{minimumFractionDigits:0,maximumFractionDigits:2})}`;
const dt=s=>s?new Date(s).toLocaleString('en-IN',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'}):'—';
// SPPS-ANDROID-001: rows are built separately so an open dialog can refresh in place.
function appointmentTab(b){
 if(new Date(b.ends_at).getTime()<Date.now()||b.status==='COMPLETED')return 'Past';
 if(['DECLINED','CANCELLED','EXPIRED','REFUNDED'].includes(b.status))return 'Declined';
 return ['CONFIRMED','APPROVED_AWAITING_PAYMENT','REFUND_PENDING'].includes(b.status)?'Approved':'Pending';
}
const appointmentAction=(id,op,label,cls='btn small')=>button(label,'client-booking-action',cls,`data-id="${id}" data-op="${op}"`);
function appointmentVideo(b){
 const now=Date.now();
 return b.status==='CONFIRMED'&&b.video_requested&&!b.video_closed&&now>=Date.parse(b.starts_at)-15*60000&&now<=Date.parse(b.ends_at)+15*60000
  ?appointmentAction(b.booking_id,'video','Join video','btn small primary')+appointmentAction(b.booking_id,'end-video','End video'):'';
}
function clientBookingRows(rows,slots){
 const open=slots.filter(s=>s.status==='available'&&Date.parse(s.starts_at)>Date.now()+2*3600000);
 const body=rows.map(b=>{
  const future=Date.parse(b.starts_at)>Date.now();
  const act=(op,label,cls)=>appointmentAction(b.booking_id,op,label,cls);
  const reasons=['','Unavailable at this time','Service unavailable','Please choose another time'];
  const decline=`<label class="small">Decline reason<select name="decline_reason" aria-label="Decline reason">${reasons.map(r=>`<option value="${esc(r)}">${esc(r||'No reason')}</option>`).join('')}</select></label>`;
  const pick=`<select name="reschedule_slot" aria-label="New time"><option value="">New time…</option>${open.filter(s=>s.id!==b.slot_id).map(s=>`<option value="${s.id}">${esc(dt(s.starts_at))}</option>`).join('')}</select>`;
  let actions='';
  if(future&&b.status==='PENDING_APPROVAL')actions+=act('approve','Approve','btn small primary')+decline+act('decline','Decline');
  if(future&&['PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED'].includes(b.status))actions+=pick+act('reschedule','Propose new time')+act('cancel','Cancel');
  actions+=`<span data-appointment-video="${b.booking_id}">${appointmentVideo(b)}</span>`;
  if(b.status==='CONFIRMED'&&b.video_closed)actions+='<span class="muted small" data-video-ended>Video ended</span>';
  return `<tr data-booking-row="${b.booking_id}"><td>${esc(b.service_name||'Appointment')}</td><td>${esc(dt(b.starts_at))} – ${esc(dt(b.ends_at))}</td><td>${b.first_request?'—':esc(b.mode==='online'||b.video_requested?'Online':'In person')}</td><td>${esc(b.subject_display_name)}${b.first_request?' '+badge('First request','gold'):''}</td><td>${badge(BOOKING_LABEL[b.status]||b.status,b.status==='CONFIRMED'?'good':'')}</td><td><div class="inline-actions">${actions}</div></td></tr>`;
 }).join('');
 return {open,body};
}
function appointmentsPanel(){
 const tabs=['Pending','Approved','Declined','Past'];
 const rows=state.appointments||[],selected=rows.filter(b=>appointmentTab(b)===state.appointmentTab);
 const {body}=clientBookingRows(selected,state.slots);
 return `<section class="card" data-appointments><div class="inline-actions" role="tablist" aria-label="Appointments">${tabs.map(t=>`<button class="btn ${state.appointmentTab===t?'primary':''}" role="tab" aria-selected="${state.appointmentTab===t}" data-action="appointment-tab" data-tab="${t}">${t} (${rows.filter(b=>appointmentTab(b)===t).length})</button>`).join('')}</div><div class="table-wrap"><table class="data-table"><thead><tr><th>Service</th><th>When (local time)</th><th>Mode</th><th>Athlete</th><th>Status</th><th>Actions</th></tr></thead><tbody>${body||'<tr><td colspan="6" class="muted">No appointments in this tab.</td></tr>'}</tbody></table></div></section>`;
}
async function clientBookingsDialog(){const [rows,slots]=await Promise.all([api('/api/booking-requests'),api('/api/slots')]);state.appointments=rows;const {open,body}=clientBookingRows(rows,slots);openDialog('Client bookings',`<div class="table-wrap" data-live="client-bookings"><table class="data-table"><thead><tr><th>Service</th><th>When (local time)</th><th>Mode</th><th>Athlete</th><th>Status</th><th>Actions</th></tr></thead><tbody data-live-body>${body||'<tr><td colspan="6" class="muted">No appointment requests yet.</td></tr>'}</tbody></table></div><form data-form="client-slot" class="card" style="margin-top:14px"><h3 style="margin-top:0">Add availability</h3><div class="field-grid"><div class="field"><label>Start<input name="starts_at" type="datetime-local" required></label></div><div class="field"><label>Minutes<input name="duration_minutes" type="number" min="20" max="120" value="50" required></label></div><div class="field"><label>${state.freeAccess?'Free appointments · ₹0':'Fee (₹)<input name="amount_rupees" type="number" min="100" step="1" value="1500" required>'}</label></div></div><button class="btn primary" type="submit">Add slot</button><p class="muted small"><span data-live-open>${open.length}</span> open slot(s). Linked clients request appointment times. You approve their requests.</p></form>`,'Requests come from athletes or authorised guardians. '+(state.freeAccess?'No payment is required.':'Payment is requested after you approve.'))}
// Stage 13A.7/13A.8: practitioner controls for athlete/guardian portal linking.
async function portalAccessDialog(newCode=null){const aid=state.athleteId;if(!aid)return;const links=await api(`/api/athletes/${aid}/portal-links`);const rows=links.map(l=>`<tr data-portal-link="${esc(l.link_id??'invite')}"><td>${badge(l.status,l.status==='ACTIVE'?'good':'')}</td><td>${esc(l.subject_display_name||'—')}${l.via_guardian?' <span class="muted small">(via guardian)</span>':''}</td><td>${l.status==='INVITED'?`Code expires ${when(l.invitation_expires_at)}`:l.consent_current?'Consent current':'<strong>Consent not current</strong>'}</td><td>${l.status==='PRACTITIONER_REVIEW'?button('Approve','portal-link-decision','btn small primary',`data-id="${l.link_id}" data-decision="approve"`)+button('Decline','portal-link-decision','btn small',`data-id="${l.link_id}" data-decision="decline"`):''}${['ACTIVE','CONSENT_PENDING'].includes(l.status)?button('Revoke','portal-link-decision','btn small',`data-id="${l.link_id}" data-decision="revoke"`):''}</td></tr>`).join('');const code=newCode?`<div class="card" data-portal-code="${esc(newCode)}"><span class="eyebrow">One-time invitation code — shown once</span><h2 style="letter-spacing:.12em;margin:6px 0">${esc(newCode)}</h2><p class="muted small">Give this code to the athlete (or their verified guardian) in person. It expires in 7 days and replaces any earlier unused code.</p></div>`:'';openDialog('Athlete portal access',`${code}<div class="table-wrap"><table class="data-table"><thead><tr><th>Status</th><th>Person</th><th>Consent</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="4" class="muted">No portal link yet.</td></tr>'}</tbody></table></div><div class="inline-actions">${button('Issue invitation code','portal-invite','btn primary')}</div>`,'Linking lets this athlete see only material you explicitly release. Private notes, draft reports and assessment narratives are never shared.')}
function optionList(items,selected=null){return items.map(x=>`<option value="${esc(x.id)}" ${String(x.id)===String(selected)?'selected':''}>${esc(x.title||x.display_name||x.name||x.label)}</option>`).join('')}
function athleteDialog(){openDialog('Create athlete record',`<form data-form="athlete"><div class="field-grid"><div class="field"><label>Athlete ID (prefer pseudonymous)</label><input name="code" required placeholder="e.g. MC-004" pattern="[A-Za-z0-9_-]+"></div><div class="field"><label>Display name</label><input name="display_name" required></div><div class="field"><label>Sport</label><input name="sport" required></div><div class="field"><label>Group</label><select name="athlete_group"><option>not specified</option><option>Youth</option><option>Adult</option><option>Elite</option><option>Recreational</option></select></div></div><label class="checkline"><input type="checkbox" name="service_consent_recorded"><span class="check-copy">I have independently recorded consent for creating and using this confidential service record.</span></label><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Save athlete</button></div></form>`)}
function newCaseDialog(){if(!state.athleteId){notify('Select an athlete first');return}openDialog('Open a case',`<form data-form="case"><input type="hidden" name="athlete_id" value="${state.athleteId}"><div class="field"><label>Case title</label><input name="title" placeholder="e.g. Competition routine" required></div><div class="field"><label>Focus</label><select name="category"><option>Performance psychology</option><option>Injury psychology</option><option>Competition preparation</option><option>General support</option></select></div><div class="field"><label>Goal</label><textarea name="goal" maxlength="1200" placeholder="Agreed working goal"></textarea></div><div class="field"><label>Next review (optional)</label><input type="date" name="next_review"></div><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Create case</button></div></form>`)}
function noteDialog(){if(!state.caseId){notify('Select a case first');return}openDialog('Document consultation',`<form data-form="note"><input type="hidden" name="case_id" value="${state.caseId}"><div class="field"><label>Session date</label><input type="date" name="note_date" required value="${fdate()}"></div><div class="field"><label>Confidential practitioner note</label><textarea name="content" required minlength="5" rows="8" placeholder="Purpose · observations · interpretation · agreed plan · follow-up"></textarea></div><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Save private draft</button></div></form>`)}
function factorDialog(){const k=state.view==='assessments'?'assessment':'monitoring';openDialog('Define measurement factor',`<form data-form="factor"><div class="field-grid"><div class="field"><label>Factor / sub-factor</label><input name="label" required placeholder="e.g. Self-confidence"></div><div class="field"><label>Parent assessment or group</label><input name="group_name" value="${k==='assessment'?'Custom assessment':'Daily monitoring'}" required></div><div class="field"><label>Category</label><select name="kind"><option ${k==='monitoring'?'selected':''} value="monitoring">Daily monitoring</option><option ${k==='assessment'?'selected':''} value="assessment">Assessment / offline score</option></select></div><div class="field"><label>Units</label><input name="unit" value="points"></div><div class="field"><label>Minimum</label><input type="number" name="low" value="0" step="any" required></div><div class="field"><label>Maximum</label><input type="number" name="high" value="10" step="any" required></div><div class="field"><label>Higher scores indicate</label><select name="higher_is"><option value="contextual">Context-dependent</option><option value="higher-supportive">More desirable</option><option value="lower-supportive">Less desirable</option></select></div></div><p class="small">Custom measures are not validated tests. Preserve scoring versions; do not mix incompatible units.</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Save factor</button></div></form>`)}
function observationDialog(){if(state.athleteLoading){notify('Athlete selection is loading. Wait before recording.');return}if(!state.athleteId){notify('Select an athlete first in Athletes & Cases');return}if(!state.factors.length){notify('Define a factor first');return}let defaultKind=state.view==='assessments'?'assessment':'monitoring',sorted=[...state.factors].sort((a,b)=>Number(b.kind===defaultKind)-Number(a.kind===defaultKind));openDialog('Record score or monitoring value',`<form data-form="observation"><div class="field-grid"><div class="field"><label>Athlete (change selection on the Monitoring page)</label><input readonly value="${esc(state.athletes.find(a=>a.id===state.athleteId)?.display_name||'Selected athlete')} (${state.athleteId})"><input type="hidden" name="athlete_id" value="${state.athleteId}"></div><div class="field"><label>Linked case (optional)</label><select name="case_id"><option value="">No case selected</option>${(state.athleteDetail?.cases||[]).map(c=>`<option value="${c.id}" ${c.id===state.caseId?'selected':''}>${esc(c.title)}</option>`).join('')}</select></div><div class="field"><label>Factor</label><select name="factor_id">${sorted.map(f=>`<option value="${f.id}">${esc(f.group_name)}: ${esc(f.label)} (${f.low}–${f.high})</option>`).join('')}</select></div><div class="field"><label>Score</label><input name="value" type="number" step="any" required></div><div class="field"><label>Observation date</label><input type="date" name="observed_on" required value="${fdate()}"></div><div class="field"><label>Context (optional)</label><input name="context" maxlength="120" placeholder="e.g. Pre-tournament"></div></div><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Record value</button></div></form>`)}
function assignDialog(){if(!state.caseId){notify('Select a case first');return}openDialog('Assign intervention',`<form data-form="assignment"><input type="hidden" name="athlete_id" value="${state.athleteId}"><input type="hidden" name="case_id" value="${state.caseId}"><div class="field"><label>Intervention</label><select name="intervention_id">${state.interventions.map(i=>`<option value="${i.id}">${esc(i.name)}</option>`).join('')}</select></div><div class="field"><label>Individualised instructions / dose</label><textarea name="dosage" rows="4" required placeholder="e.g. 5 minutes before practice, 3 days weekly; review response next session"></textarea></div><p class="small">Review suitability, athlete preferences and evidence before assigning. This is practitioner-directed.</p><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Assign</button></div></form>`)}
function reportDialog(){if(!state.caseId){notify('Select an athlete case first');return}openDialog('New private report draft',`<form data-form="report"><input type="hidden" name="athlete_id" value="${state.athleteId}"><input type="hidden" name="case_id" value="${state.caseId}"><div class="field"><label>Report title</label><input name="title" required value="Progress review"></div><div class="field"><label>Intended audience</label><select name="audience"><option value="practitioner">Practitioner only</option><option value="athlete">Athlete (subject to approval)</option><option value="coach">Coach (subject to disclosure approval)</option></select></div><div class="field"><label>Summary content (private draft)</label><textarea name="body" rows="7" required placeholder="Observations, progress, limitations, next steps. Exclude sensitive disclosures from coach-targeted drafts."></textarea></div><div class="form-actions">${button('Cancel','modal-close','btn')}<button type="submit" class="btn primary">Save private draft</button></div></form>`)}
let athleteLoadSequence=0,athleteSelectionRevision=0,workspaceLoadSequence=0,clinicalWriteRevision=0;
async function loadAthlete(id,userSelected=false){
 const selectedId=Number(id),sequence=++athleteLoadSequence,writeRevision=clinicalWriteRevision;
 if(!Number.isSafeInteger(selectedId)||selectedId<1)throw Error('Choose a valid athlete');
 if(userSelected)athleteSelectionRevision++;
 const previousCaseId=state.athleteId===selectedId?state.caseId:null;
 // Commit selected identity synchronously and clear all previous-athlete data.
 Object.assign(state,{athleteId:selectedId,athleteLoading:true,athleteDetail:null,caseId:null,caseDetail:null,observations:[],reports:[],offlineAssessments:[],indices:{series:[]},injuries:[]});
 render();
 // The core overview must not depend on optional history, indices, or injury modules.
 const detail=await api(`/api/athletes/${selectedId}`).catch(err=>{if(sequence===athleteLoadSequence){state.athleteLoading=false;render()}throw err});
 if(sequence!==athleteLoadSequence||state.athleteId!==selectedId)return;
 const cases=detail.cases||[],caseId=cases.some(c=>c.id===previousCaseId)?previousCaseId:(cases[0]?.id||null);
 Object.assign(state,{athleteId:selectedId,athleteLoading:false,athleteDetail:detail,caseId,caseDetail:null,observations:[],reports:[],offlineAssessments:[],indices:{series:[]},injuries:[]});
 render(); // Show athlete overview immediately, including athletes with no case.
 const paths=[caseId?`/api/cases/${caseId}`:null,`/api/athletes/${selectedId}/observations`,
  `/api/athletes/${selectedId}/reports`,`/api/athletes/${selectedId}/assessments/offline`,
  `/api/athletes/${selectedId}/indices`,`/api/athletes/${selectedId}/injury`];
 const results=await Promise.allSettled(paths.map(path=>path?api(path):Promise.resolve(null)));
 // A saved clinical write wins over GET responses captured before that write.
 if(sequence!==athleteLoadSequence||state.athleteId!==selectedId||writeRevision!==clinicalWriteRevision)return;
 const fields=['caseDetail','observations','reports','offlineAssessments','indices','injuries'];
 const failed=[];
 results.forEach((result,i)=>{if(result.status==='fulfilled')state[fields[i]]=result.value;
  else failed.push(fields[i]);});
 render();
 if(failed.length)notify(`Athlete overview opened; some history sections could not load (${failed.join(', ')}). Refresh to retry.`);
}
async function refreshCase(){state.caseDetail=state.caseId?await api(`/api/cases/${state.caseId}`):null}
async function loadObservations(){state.observations=state.athleteId?await api(`/api/athletes/${state.athleteId}/observations`):[]}
async function loadReports(){state.reports=state.athleteId?await api(`/api/athletes/${state.athleteId}/reports`):[]}
async function loadAll(){
 const request=++workspaceLoadSequence,selectedVersion=athleteSelectionRevision;
 const [d,aa,ff,ii]=await Promise.all([api('/api/dashboard'),api('/api/athletes'),api('/api/factors'),api('/api/interventions')]);
 if(request!==workspaceLoadSequence||selectedVersion!==athleteSelectionRevision)return;
 Object.assign(state,{dashboard:d,athletes:aa,factors:ff,interventions:ii});
 if(state.lifecycle===undefined){try{state.lifecycle=await api('/api/account/lifecycle')}catch{state.lifecycle=null}}
 if(state.user?.is_admin){try{state.adminReviews=await api('/api/admin/practitioner-reviews')}catch{}await loadRegistrationSummary()}
 if(window.SPPSv2){try{await SPPSv2.load()}catch{}}
 if(aa.length){
  const selectedId=state.athleteId;
  if(selectedId&&!aa.some(a=>a.id===selectedId)){
   athleteLoadSequence++;
   Object.assign(state,{athleteId:null,athleteLoading:false,athleteDetail:null,caseId:null,caseDetail:null,observations:[],reports:[],offlineAssessments:[]});
   render();notify('Selected athlete is unavailable. Choose the correct athlete before recording.');return;
  }
  if(!state.athleteLoading)await loadAthlete(selectedId||aa[0].id);
 }else{
  athleteLoadSequence++;
  Object.assign(state,{athleteId:null,athleteLoading:false,athleteDetail:null,caseId:null,caseDetail:null,observations:[],reports:[],offlineAssessments:[]});
 }
 if(request!==workspaceLoadSequence||selectedVersion!==athleteSelectionRevision)return;
 await betaLoadAll();
}
async function openStudio(){navigateSection('studio','/studio');if(!state.user){try{const user=await api('/api/auth/me');state.user=user;state.csrf=user.csrf;if(window.SPPSPush)SPPSPush.start({scope:'workspace',csrf:state.csrf});await loadAll()}catch{}}render()}
async function action(el,evt){let a=el.dataset.action;
 if(a.startsWith('v2:')&&window.SPPSv2){await SPPSv2.action(a,el,evt);return}
 if(a==='v2-email-resend'){const r=await api(`/api/admin/email-deliveries/${Number(el.dataset.id)}/resend`,{method:'POST',body:{}});await loadRegistrationSummary();render();notify(r.status==='sent'?'Email sent':'Email still not delivered: check the mail settings');return}
 if(a==='app-back'){const depth=history.state?.[SPPS_NAV_KEY]?.depth||0;if(depth>0){history.back();return}if(state.mode==='studio'&&state.user&&state.view!=='overview'){state.view='overview';render();return}navigateSection('directory','/');history.replaceState({},'', '/');closeDialog();render();return}
 if(a==='spps-reset-request'){
   openDialog('Forgot Password?','<p>Enter your email to receive a password reset link.</p><form class="stack" data-form="spps-reset-request"><label>Email<input name="email" type="email" required></label><button type="submit" class="btn primary">Send reset link</button></form>');
   return;
 }
 if(a==='spps-refresh-admin'){if(!state.user?.is_admin)throw Error('Administrator required');state.adminReviews=await api('/api/admin/practitioner-reviews');await loadRegistrationSummary();render();return}
 if(a==='spps-admin-filter'){state.adminFilter=el.dataset.filter||'pending';render();return}
 if(a==='spps-admin-decision'){
  if(!state.user?.is_admin)throw Error('Administrator required');
  const decision=el.dataset.decision, pid=Number(el.dataset.id);
  const person=(state.adminReviews||[]).find(x=>Number(x.practitioner_id)===pid);
  const titles={approve:'Approve and list this practitioner',reject:'Reject this application',request_more_info:'Ask for more information'};
  const help={approve:'The practitioner gets workspace access and appears in Find a Sport Psychologist. The note is optional.',reject:'Workspace access is blocked and the profile is removed from the directory. The applicant sees your note when they sign in.',request_more_info:'Access is paused and the profile is hidden. The applicant sees your question when they sign in and can reply with updated details or links to documents.'};
  if(!titles[decision])throw Error('Unknown decision');
  openDialog(titles[decision],'<p><strong>'+esc(person?.name||'Practitioner')+'</strong></p><p class="small muted">'+help[decision]+'</p>'+
   '<form class="stack" data-form="spps-admin-decision" data-id="'+pid+'" data-decision="'+decision+'"><label>Note'+(decision==='approve'?' (optional)':' (required, shown to the applicant)')+'<textarea name="note" rows="4" maxlength="1500"'+(decision==='approve'?'':' required minlength="5"')+'></textarea></label>'+
   '<div class="form-actions">'+button('Cancel','modal-close','btn')+'<button class="btn primary" type="submit">Confirm</button></div></form>');
  return;
 }
 if(a==='directory-scroll'){document.getElementById('practitioner-directory')?.scrollIntoView({behavior:'smooth'});return}
 if(a==='theme'){state.theme=el.dataset.themeName;document.body.dataset.theme=state.theme;localStorage.setItem('modelc-theme',state.theme);render();return}
 if(a==='modal-close'||a==='modal-close-backdrop'){if(a==='modal-close-backdrop'&&el!==evt.target)return;closeDialog();return}
 if(a==='registration'){navigateSection('register','/register');closeDialog();render();window.scrollTo({top:0,behavior:'smooth'});return}
 if(a==='free-contact'){
  const practitionerId=Number(el.dataset.id);
  openDialog('Book an appointment','<p>Appointments are requested from your athlete or guardian account, so your details stay private and a guardian can give consent for under-18s.</p><p class="small muted">Sign in or create an account, choose a practitioner and a service, and send the request. The practitioner approves it and a video link then appears for both of you.</p><div class="form-actions">'+button('Cancel','modal-close','btn')+'<a class="btn primary" href="/portal?practitioner='+practitionerId+'">Continue to the athlete portal</a></div>');return;
 }
 if(a==='subscription-checkout'){if(window.Capacitor?.isNativePlatform?.()){notify('Subscriptions are not offered in SPPS Version 1.0 - Free. No payment was initiated.');return}const info=await api('/api/public/practitioner-registration/checkout',{method:'POST',body:{registration_token:state.registrationToken}});if(info.checkout_url){window.location.assign(info.checkout_url);return}state.registrationStatus='Subscription checkout started; contact support if no payment link appeared.';render();return}
 if(a==='subscription-status'){const current=await api('/api/public/practitioner-registration/status',{method:'POST',body:{registration_token:state.registrationToken}});state.registrationStatus=current.status==='active'?'Active! You may now sign in.':'Subscription status: '+current.status;render();return}
 if(a==='public'){navigateSection('public','/services');closeDialog();render();return}
 if(a==='directory'){navigateSection('directory','/');closeDialog();try{[state.practitioners,state.publicProgrammes]=await Promise.all([api('/api/public/practitioners'),api('/api/public/programmes').catch(()=>[])])}catch{state.practitioners=[]}render();window.scrollTo({top:0,behavior:'smooth'});return}
 if(a==='studio'){await openStudio();return}
 if(a==='logout'){let logoutError=null;try{await api('/api/auth/logout',{method:'POST'})}catch(err){logoutError=err}if(supabaseClient){try{await supabaseClient.auth.signOut({scope:'local'})}catch(_){logoutError=logoutError||Error('Supabase sign-out failed')}}clearPrivateState();state.mode='directory';history.replaceState({},'', '/');closeDialog();render();window.scrollTo({top:0,behavior:'instant'});notify(logoutError?'Workspace cleared locally, but server sign-out could not be confirmed.':'Signed out; private workspace cleared');return}
 if(a==='view'){const nextView=el.dataset.view;if(nextView==='admin'&&!state.user?.is_admin)throw Error('Administrator required');workspaceNavigation({view:nextView});state.view=nextView;if(['athletes','bookings'].includes(state.view))await loadAll();if(state.view==='settings')await refreshOrganisationQuotes();if(state.view==='admin')state.adminReviews=await api('/api/admin/practitioner-reviews');render();return}
 if(a==='refresh-organisation-quotes'){await refreshOrganisationQuotes();render();notify(state.organisationQuotesUnavailable?'Institutional enquiries are not available in this release.':'Institutional enquiries refreshed');return}
 if(a==='refresh-data'){await loadAll();render();notify('Latest practitioner records loaded');return}
 if(a==='open-athlete-overview'){if(!state.athleteId){notify('Select an athlete first');return}workspaceNavigation({view:'athletes'});state.view='athletes';await loadAthlete(state.athleteId);render();document.getElementById('athlete-overview-panel')?.scrollIntoView({block:'start',behavior:'smooth'});return}
 if(a==='athlete-assessments'){workspaceNavigation({view:'assessments'});state.view='assessments';render();return}
 if(a==='athlete-monitoring'){workspaceNavigation({view:'monitoring'});state.view='monitoring';render();return}
 if(a==='refresh-clinical'){if(!state.athleteId){notify('Select an athlete first');return}await loadAthlete(state.athleteId);render();notify('Latest assessment and monitoring records loaded from server');return}
 if(a==='public-slots'){const pid=Number(el.dataset.id);const slots=await api(`/api/public/practitioners/${pid}/slots`);if(!slots.length){notify('No appointment slots currently available');return}betaBookingDialog(pid,slots);return}
 if(a==='open-bookings'){workspaceNavigation({view:'bookings'});state.view='bookings';await loadAll();render();return}
 if(a==='my-public-profile'){navigateSection('directory','/');const [people,packs]=await Promise.all([api('/api/public/practitioners'),api('/api/public/programmes')]);state.practitioners=people;state.publicProgrammes=packs;render();const node=document.getElementById(`practitioner-${state.user?.id}`);if(node)node.scrollIntoView({behavior:'smooth',block:'center'});else notify('Your directory listing is not currently published. Check membership and credential-review status.');return}
 if(a==='organisation-quote'){organisationQuoteDialog();return}
 if(a==='programme-request'){programmeRequestDialog(Number(el.dataset.id));return}
 if(a==='new-programme'){programmeDialog();return}
 if(a==='toggle-programme'){await api(`/api/practitioner/programmes/${el.dataset.id}`,{method:'PATCH',body:{status:el.dataset.status}});state.ownProgrammes=await api('/api/practitioner/programmes');render();notify('Programme visibility updated');return}
 if(a==='programme-contacted'){await api(`/api/practitioner/programme-enquiries/${el.dataset.id}`,{method:'PATCH',body:{status:'contacted'}});state.programmeEnquiries=await api('/api/practitioner/programme-enquiries');render();notify('Enquiry marked contacted');return}
 if(a==='new-slot'){betaSlotDialog();return}
 if(a==='offline-assessment'){betaOfflineDialog();return}
 if(a==='new-injury'){betaInjuryDialog();return}
 if(a==='injury-checkin'){betaInjuryCheckDialog(Number(el.dataset.id));return}
 if(a==='profile-edit'){betaProfileDialog();return}
 if(a==='account-deactivate'){accountDeactivateDialog();return}
 if(a==='account-export'){accountExportDialog();return}
 if(a==='account-reactivate'){state.lifecycle=await api('/api/account/reactivate',{method:'POST',body:{confirm:true}});render();notify('Your listing is visible again');return}
 if(a==='approve-booking'){await api(`/api/bookings/${el.dataset.id}/approve`,{method:'POST'});state.bookings=await api('/api/bookings');render();notify('Consultation approved');return}
 if(a==='session-review'){const id=Number(el.dataset.id);const rows=await api(`/api/rooms/${id}/reviews`);openDialog('Post-session observations',`<p class="small muted">Practitioner observations and separately solicited athlete self-reports only. Facial-expression emotion estimates are not computed.</p>${rows.map(r=>`<div class="strip">${esc(r.time_offset_seconds)}s · ${esc(r.kind)} · ${esc(r.note)}</div>`).join('')}<form data-form="session-review" data-id="${id}"><div class="field"><label>Time offset (seconds)</label><input name="time_offset_seconds" type="number" min="0" value="0"></div><div class="field"><label>Source</label><select name="kind"><option value="practitioner_observation">Practitioner observation</option><option value="client_self_report">Client self-report (documented by practitioner)</option></select></div><div class="field"><label>Observation</label><textarea name="note" required></textarea></div><div class="form-actions"><button class="btn primary" type="submit">Save observation</button></div></form>`);return}
 if(a==='export-report'){const kind=el.dataset.format;const id=el.dataset.id;const res=await fetch(`/api/reports/${id}/export?format=${encodeURIComponent(kind)}`,{credentials:'same-origin'});if(!res.ok)throw Error(`Export failed (${res.status})`);const blob=await res.blob();const href=URL.createObjectURL(blob);const link=document.createElement('a');link.href=href;link.download=`spps-report-${id}.${kind}`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),2000);return}
 if(a==='complete-booking'){
   if(!confirm('Confirm this consultation is finished? This revokes both video invitation tokens and permanently ends the room.'))return;
   await api(`/api/bookings/${el.dataset.id}/complete`,{method:'POST'});
   state.bookings=await api('/api/bookings');state.slots=await api('/api/slots');
   render();notify('Consultation completed. Room invitations revoked.');return;
 }
 if(a==='room-invite'){
   const info=await api(`/api/bookings/${el.dataset.id}/room-invite`,{method:'POST'});
   const practitionerPath=`/room/${info.room_id}?access=${encodeURIComponent(info.practitioner_token)}`;
   const guestUrl=`${location.origin}/room/${info.room_id}?access=${encodeURIComponent(info.guest_token)}`;
   openDialog('Consent-gated video invitation',`<p>Join securely as the practitioner. Share the guest invitation only with the verified booking contact. Anyone holding that link may join after confirming consent. No recording or facial emotion analysis is enabled.</p><div class="invite-panel"><h3>Practitioner</h3><p class="small muted">Open your consultation room on this device.</p><a class="btn primary block" href="${esc(practitionerPath)}">Open practitioner room</a></div><div class="invite-panel"><h3>Guest invitation</h3><p class="small muted">Invitation contains a confidential access token. Its full URL is hidden here to prevent accidental exposure.</p><div class="invite-actions"><button type="button" class="btn" data-action="copy-room-invite" data-invite-url="${esc(guestUrl)}">Copy guest invitation</button><button type="button" class="btn" data-action="share-room-invite" data-invite-url="${esc(guestUrl)}">Share privately</button></div></div>`);return;
 }
 if(a==='copy-room-invite'){
   const value=el.dataset.inviteUrl;
   if(!value)return;
   try{await navigator.clipboard.writeText(value);notify('Guest invitation copied. Share it only with the verified booking contact.')}catch{window.prompt('Copy invitation (keep private)',value)}
   return;
 }
 if(a==='share-room-invite'){
   const value=el.dataset.inviteUrl;
   if(!value)return;
   if(navigator.share){try{await navigator.share({title:'SPPS private consultation invitation',url:value})}catch(err){if(err.name!=='AbortError')notify('Sharing unavailable. Use Copy guest invitation instead.')}}else{notify('Sharing is unavailable on this device. Use Copy guest invitation instead.')}
   return;
 }


 if(a==='start-payment'){await betaCheckout(Number(el.dataset.id));return}
 if(a==='new-athlete'){athleteDialog();return}
 if(a==='new-case'){newCaseDialog();return}
 if(a==='portal-access'){await portalAccessDialog();return}
 if(a==='appointment-tab'){state.appointmentTab=el.dataset.tab;render();return}
 if(a==='client-bookings'){await clientBookingsDialog();return}
 if(a==='practitioner-reviews'){await practitionerReviewsDialog();return}
 if(a==='licence-revoke'){if(!confirm('Revoke this seat? The practitioner keeps access only if they hold their own membership.'))return;await api(`/api/admin/licences/${Number(el.dataset.id)}/seats/${Number(el.dataset.pid)}/revoke`,{method:'POST'});await practitionerReviewsDialog();return}
 if(a==='practitioner-decision'){const reason=prompt('Reason (recorded in the audit trail, at least 5 characters):')||'';if(reason.trim().length<5){notify('A reason of at least 5 characters is required');return}await api(`/api/admin/practitioners/${Number(el.dataset.id)}/${el.dataset.gate}`,{method:'POST',body:{decision:el.dataset.decision,reason}});await practitionerReviewsDialog();notify('Decision recorded');return}
 if(a==='participation'){await participationDialog();return}
 if(a==='programme-status'){await api(`/api/programmes/${Number(el.dataset.id)}/status`,{method:'POST',body:{status:el.dataset.status}});await participationDialog();notify('Programme updated');return}
 if(a==='task-discontinue'){if(!confirm('Discontinue this task? The athlete will no longer see it; past submissions are kept.'))return;await api(`/api/programme-tasks/${Number(el.dataset.id)}/discontinue`,{method:'POST'});await participationDialog();return}
 if(a==='summary-withdraw'){if(!confirm('Withdraw this released summary from the portal?'))return;await api(`/api/released-summaries/${Number(el.dataset.id)}/withdraw`,{method:'POST'});await participationDialog();return}
 if(a==='client-booking-action'){const id=Number(el.dataset.id),act=el.dataset.op;let body={};if(act==='decline'||act==='cancel'){const reason=act==='decline'?(el.closest('[data-booking-row]').querySelector('[name=decline_reason]')?.value||''):(prompt('Reason for cancelling:')||'');if(act==='cancel'&&reason.trim().length<5){notify('A reason of at least 5 characters is required');return}body={reason}}if(act==='video'){const r=await api(`/api/booking-requests/${id}/video-ticket`,{method:'POST'});window.Capacitor?.isNativePlatform?.()?location.assign(r.room_url):window.open(r.room_url,'_blank','noopener');return}if(act==='end-video'){if(!confirm('End the video session for both participants? The room closes and cannot be reopened for this booking.'))return;await api(`/api/booking-requests/${id}/end-video`,{method:'POST'});state.appointments=await api('/api/booking-requests');if(state.view==='bookings'&&!modal.innerHTML)render();else await clientBookingsDialog();notify('Video session ended');return}if(act==='approve'||act==='decline')await api(`/api/booking-requests/${id}/decision`,{method:'POST',body:{decision:act,...body}});else if(act==='reschedule'){const slot=Number(el.closest('[data-booking-row]').querySelector('select[name="reschedule_slot"]')?.value||0);if(!slot){notify('Choose an open slot first');return}await api(`/api/booking-requests/${id}/reschedule`,{method:'POST',body:{slot_id:slot}})}else await api(`/api/booking-requests/${id}/${act}`,{method:'POST',body:act==='cancel'?body:undefined});if(state.view==='bookings'&&!modal.innerHTML){state.appointments=await api('/api/booking-requests');state.slots=await api('/api/slots');render()}else await clientBookingsDialog();notify('Booking updated');return}
 if(a==='portal-invite'){const r=await api(`/api/athletes/${state.athleteId}/portal-invitations`,{method:'POST'});await portalAccessDialog(r.code);return}
 if(a==='portal-link-decision'){const d=el.dataset.decision;if(d!=='approve'&&!confirm(d==='revoke'?'Revoke this portal link? The athlete or guardian loses portal access to this record immediately.':'Decline this link request?'))return;await api(`/api/portal-links/${Number(el.dataset.id)}/decision`,{method:'POST',body:{decision:d}});await portalAccessDialog();notify(`Portal link ${d}d`);return}
 if(a==='record-consent'){if(!confirm('Confirm independent service intake consent has been documented in accordance with your practice policy?'))return;await api(`/api/athletes/${state.athleteId}/service-consent`,{method:'POST',body:{confirmed:true}});await loadAthlete(state.athleteId);render();notify('Service consent recorded');return}
 if(a==='new-note'){noteDialog();return}
 if(a==='new-factor'){factorDialog();return}
 if(a==='new-observation'){observationDialog();return}
 if(a==='assign-intervention'){assignDialog();return}
 if(a==='new-report'){reportDialog();return}
 if(a==='convert'){convertDialog(Number(el.dataset.id));return}
 if(a==='athlete'){workspaceNavigation({view:'athletes',athleteId:Number(el.dataset.id),caseId:null});state.view='athletes';await loadAthlete(Number(el.dataset.id),true);render();if(window.matchMedia('(max-width:900px)').matches)document.getElementById('athlete-overview-panel')?.scrollIntoView({block:'start',behavior:'smooth'});return}
 if(a==='case'){workspaceNavigation({caseId:Number(el.dataset.id)});state.caseId=Number(el.dataset.id);await refreshCase();render();return}
 if(a==='finalize-note'){if(!confirm('Finalise this note? It will become immutable in this build.'))return;await api(`/api/notes/${el.dataset.id}/finalize`,{method:'POST',body:{expected_revision:Number(el.dataset.revision)}});await refreshCase();render();notify('Note finalised');return}
}
async function verifyClinicalWrite(kind,athleteId,created){
 // POST returned 201; an older GET must never erase the new record.
 // Keep its ID even if readback fails; a blind retry could duplicate clinical entries.
 const writeRevision=++clinicalWriteRevision;
 const isAssessment=kind==='assessment';const url=`/api/athletes/${athleteId}/${isAssessment?'assessments/offline':'observations'}`;
 try{
  const records=await api(url,{cache:'no-store'});
  if(!records.some(row=>row.id===created.id))throw Error('New record was absent from server read-back');
  if(writeRevision===clinicalWriteRevision && athleteId===state.athleteId){
   if(isAssessment)state.offlineAssessments=records;else state.observations=records;
  }
  closeDialog();render();notify(`${isAssessment?'Assessment':'Monitoring'} #${created.id} saved and verified from server`);
 }catch(err){
  closeDialog();render();notify(`Server accepted ${isAssessment?'assessment':'monitoring'} #${created.id}, but confirmation failed (${err.message}). Refresh before retrying.`);
 }
}
async function submit(form){const type=form.dataset.form;const raw=(key)=>val(form,key);
 if(type.startsWith('v2:')&&window.SPPSv2){await SPPSv2.submit(type,form,raw);return}
 if(type==='v2-application-photo'){const sb=await requireSupabase();const {data:sessionData}=await sb.auth.getSession();const token=sessionData?.session?.access_token;if(!token)throw Error('Your session has ended. Sign in to add your photo.');const file=form.elements.namedItem('photo').files?.[0];if(!file)throw Error('Choose a photo');await api('/api/public/practitioner-application/photo',{method:'POST',body:{access_token:token,photo_base64:await profilePhotoBase64(file)}});state.photoAfterConfirm=false;await finishApplication(sb);state.mode='studio';navigateSection('studio','/studio');render();notify('Your application is complete');return}

 if(type==='spps-admin-decision'){
  if(!state.user?.is_admin)throw Error('Administrator required');
  const decision=form.dataset.decision,pid=Number(form.dataset.id);
  const note=(raw('note')||'').trim()||(decision==='approve'?'Approved after credential review.':'');
  if(note.length<5)throw Error('Add a note of at least five characters');
  const result=await api('/api/admin/practitioners/'+pid+'/approval',{method:'POST',body:{decision,note}});
  state.adminReviews=await api('/api/admin/practitioner-reviews');closeDialog();render();
  notify('Saved. '+(result.notification_sent?'The applicant was also notified by email.':'The applicant will see your note when they sign in.'));
  return;
 }
 if(type==='application-update'){
  const sb=await requireSupabase();
  const {data:sessionData}=await sb.auth.getSession();
  if(!sessionData?.session?.access_token)throw Error('Sign in again to update your application');
  const result=await api('/api/public/practitioner-application/update',{method:'POST',body:{
   access_token:sessionData.session.access_token,qualifications:raw('qualifications'),
   experience_years:Number(raw('experience_years')),achievements:raw('achievements'),specialisms:raw('specialisms')}});
  closeDialog();notify(result.message);return;
 }
 if(type==='spps-reset-request'){
  const sb=await requireSupabase();
  const {error}=await sb.auth.resetPasswordForEmail(raw('email'),{redirectTo:window.location.origin+'/studio'});
  if(error)throw Error(error.message);
  closeDialog();notify('If the account exists, password-reset instructions will arrive by email.');return;
 }
 if(type==='spps-change-password'){
  const sb=await requireSupabase();
  const {error}=await sb.auth.updateUser({password:raw('password')});
  if(error)throw Error(error.message);
  state.passwordRecovery=false;render();notify('Password updated. Sign in with your email and new password.');return;
 }


 if(type==='licence-new'){await api('/api/admin/licences',{method:'POST',body:{organisation_name:raw('organisation_name'),seats:Number(raw('seats'))}});await practitionerReviewsDialog();notify('Licence created');return}
 if(type==='licence-seat'){await api(`/api/admin/licences/${Number(form.dataset.id)}/seats`,{method:'POST',body:{practitioner_id:Number(raw('practitioner_id'))}});await practitionerReviewsDialog();notify('Seat assigned');return}
 if(type==='programme-new'){await api(`/api/athletes/${state.athleteId}/programmes`,{method:'POST',body:{title:raw('title'),goal:raw('goal')}});await participationDialog();notify('Draft programme created');return}
 if(type==='task-new'){await api(`/api/programmes/${Number(form.dataset.id)}/tasks`,{method:'POST',body:{title:raw('title'),instructions:raw('instructions'),frequency:raw('frequency')}});await participationDialog();notify('Task added');return}
 if(type==='submission-review'){await api(`/api/task-submissions/${Number(form.dataset.id)}/review`,{method:'POST',body:{feedback:raw('feedback')}});await participationDialog();notify('Feedback sent');return}
 if(type==='summary-release'){if(!confirm('Release this summary to the athlete/guardian portal? Only this text is shared.'))return;await api(`/api/athletes/${state.athleteId}/released-summaries`,{method:'POST',body:{kind:raw('kind'),title:raw('title'),body:raw('body')}});await participationDialog();notify('Summary released');return}
 if(type==='client-slot'){const start=new Date(raw('starts_at'));if(isNaN(start)){notify('Choose a valid date and time');return}await api('/api/slots',{method:'POST',body:{starts_at:start.toISOString(),duration_minutes:Number(raw('duration_minutes')),amount_paise:state.freeAccess?0:Math.round(Number(raw('amount_rupees'))*100)}});await clientBookingsDialog();notify('Availability added');return}
 if(type==='practitioner-programme'){await api('/api/practitioner/programmes',{method:'POST',body:{title:raw('title'),duration_weeks:Number(raw('duration_weeks')),sessions_included:Number(raw('sessions_included')),deliverables:raw('deliverables'),amount_paise:Math.round(Number(raw('amount_rupees'))*100)}});state.ownProgrammes=await api('/api/practitioner/programmes');closeDialog();state.view='settings';render();notify('Programme listed. Enquiries only; checkout is not implemented.');return}
 if(type==='programme-request'){const result=await api('/api/public/programme-enquiries',{method:'POST',body:{programme_id:Number(raw('programme_id')),contact_name:raw('contact_name'),contact_email:raw('contact_email'),message:raw('message'),contact_consent:form.elements.namedItem('contact_consent').checked,is_minor:form.elements.namedItem('is_minor').checked,guardian_confirmed:form.elements.namedItem('guardian_confirmed').checked,website:raw('website')}});closeDialog();notify(`${result.reference}: enquiry received. No payment taken and no appointment reserved.`);return}
 if(type==='organisation-quote'){const result=await api('/api/public/organisation-quote',{method:'POST',body:{organisation_name:raw('organisation_name'),contact_name:raw('contact_name'),contact_email:raw('contact_email'),practitioner_seats:Number(raw('practitioner_seats')),athletes_estimate:Number(raw('athletes_estimate')),requirements:raw('requirements'),contact_consent:form.elements.namedItem('contact_consent').checked,website:raw('website')}});closeDialog();notify(`${result.reference}: annual quotation request received. No payment taken.`);return}
 if(type==='beta-book'){const b=await api('/api/public/bookings',{method:'POST',body:{slot_id:Number(raw('slot_id')),contact_name:raw('contact_name'),contact_email:raw('contact_email'),is_minor:form.elements.namedItem('is_minor').checked,guardian_confirmed:form.elements.namedItem('guardian_confirmed').checked,contact_consent:form.elements.namedItem('contact_consent').checked,video_consent:form.elements.namedItem('video_consent').checked}});closeDialog();openDialog('Booking request created',`<p>Booking #${b.booking_id} · ₹${(b.amount_paise/100).toFixed(2)} · ${esc(b.status)}</p><p>No consultation is confirmed until verified payment and practitioner approval.</p>${button('Pay online','start-payment','btn primary',`data-id="${b.booking_id}"`)}`);return}
 if(type==='session-review'){await api(`/api/rooms/${form.dataset.id}/reviews`,{method:'POST',body:{time_offset_seconds:Number(raw('time_offset_seconds')),kind:raw('kind'),note:raw('note')}});closeDialog();render();notify('Session observation saved');return}
 if(type==='appointment-slot'){const local=new Date(raw('starts_at'));if(!Number.isFinite(local.getTime()))throw Error('Invalid start time');await api('/api/slots',{method:'POST',body:{starts_at:local.toISOString(),duration_minutes:Number(raw('duration_minutes')),amount_paise:state.freeAccess?0:Math.round(Number(raw('amount_rupees'))*100)}});state.bookings=await api('/api/bookings');state.slots=await api('/api/slots');closeDialog();render();notify('Appointment slot published');return}
 if(type==='offline-assessment'){if(state.athleteLoading)throw Error('Wait for the selected athlete before saving.');const scores=raw('scores').split('\n').filter(Boolean).map(line=>{const parts=line.split('|').map(x=>x.trim());if(parts.length!==3||!Number.isFinite(Number(parts[2])))throw Error('Use: Sub-factor | Unit | Numeric value');return {parent_factor:raw('parent_factor'),sub_factor:parts[0],unit:parts[1],value:Number(parts[2])}});const athleteId=Number(form.dataset.athleteId),caseId=Number(form.dataset.caseId);if(!athleteId||!caseId)throw Error('Select an athlete and case first');if(athleteId!==state.athleteId||caseId!==state.caseId||state.athleteDetail?.athlete?.id!==athleteId)throw Error('Athlete or case changed. Reopen the assessment form.');const saved=await api('/api/assessments/offline',{method:'POST',body:{athlete_id:athleteId,case_id:caseId,instrument_name:raw('instrument_name'),instrument_version:raw('instrument_version'),observed_on:raw('observed_on'),note:raw('note'),scores}});await verifyClinicalWrite('assessment',athleteId,saved);return}
 if(type==='injury-episode'){await api('/api/injury/episodes',{method:'POST',body:{athlete_id:state.athleteId,case_id:state.caseId,injury_label:raw('injury_label'),start_date:raw('start_date'),stage:raw('stage')}});await betaLoadAthlete();closeDialog();render();notify('Injury psychology episode created');return}
 if(type==='injury-checkin'){await api(`/api/injury/episodes/${form.dataset.id}/checkins`,{method:'POST',body:{observed_on:raw('observed_on'),pain:Number(raw('pain')),fear_of_reinjury:Number(raw('fear_of_reinjury')),confidence:Number(raw('confidence')),coping:Number(raw('coping')),note:raw('note')}});await betaLoadAthlete();closeDialog();render();notify('Rehabilitation check-in recorded');return}
 if(type==='account-deactivate'){state.lifecycle=await api('/api/account/deactivate',{method:'POST',body:{confirm:true}});closeDialog();render();notify('Your listing is offline. Your workspace is unchanged.');return}
 if(type==='account-export'){await accountExportDownload(raw('password'));closeDialog();notify('Export downloaded. It contains confidential client information: keep it secure.');return}
 if(type==='profile-edit'){await api('/api/practitioner/profile',{method:'PUT',body:{qualifications:raw('qualifications'),experience_years:Number(raw('experience_years')),achievements:raw('achievements'),specialisms:raw('specialisms')}});state.privateProfile=await api('/api/practitioner/profile');closeDialog();render();notify('Submitted for verification; public listing temporarily hidden');return}
 if(type==='practitioner-register'){
    let supabase_access_token=null;
    if(raw('email').trim().toLowerCase()!==OWNER_EMAIL){
      // Applicants must supply the details the administrator reviews; the platform owner only needs an account.
      if(raw('name').trim().length<3)throw Error('Enter your full name');
      if(!form.elements.namedItem('photo').files?.[0])throw Error('Profile photo is required');
      if(raw('qualifications').trim().length<4)throw Error('Describe your qualifications');
    }
    if(state.supabaseAuth){
      const sb=await requireSupabase(),email=raw('email').trim().toLowerCase();
      const {data:sessionData}=await sb.auth.getSession();
      if(!sessionData?.session){
        if(!raw('password'))throw Error('Set an email password to register');
        const isOwner=email===OWNER_EMAIL;
        // Read the photo before sign-up so an unreadable image fails here, not after the email has been sent.
        const draftPhoto=isOwner?null:await profilePhotoBase64(form.elements.namedItem('photo').files[0]);
        const accountApplication=isOwner?null:applicationForAccount({name:raw('name'),qualifications:raw('qualifications'),
          experience_years:raw('experience_years'),achievements:raw('achievements'),specialisms:raw('specialisms')});
        // v2: the application is stored on the server first; the claim travels with the Supabase account, so the
        // confirmation link works in any browser or the app. The carried copy stays as a fallback.
        const pending=isOwner?null:await api('/api/public/practitioner-applications/pending',{method:'POST',body:{email,
          name:raw('name').trim(),qualifications:raw('qualifications'),experience_years:Number(raw('experience_years'))||0,
          achievements:raw('achievements'),specialisms:raw('specialisms'),terms_accepted:true,
          form_token:state.formToken||'',website:raw('website')||''}});
        const claim=pending&&typeof pending.claim==='string'?pending.claim:null;
        const signupData={...(accountApplication?{[APPLICATION_META_KEY]:accountApplication}:{}),...(claim?{spps_claim:claim}:{})};
        const {data:signup,error}=await sb.auth.signUp({email,password:raw('password'),
          options:{emailRedirectTo:window.location.origin+'/register',...(Object.keys(signupData).length?{data:signupData}:{})}});
        if(error)throw Error(error.message);
        // Supabase answers a sign-up for an address that already has an account with no identities and sends no email.
        if(Array.isArray(signup?.user?.identities)&&signup.user.identities.length===0)throw Error('This email already has an account. Use Sign in instead.');
        if(signup?.session)await sb.auth.signOut({scope:'local'});
        state.pendingSignupEmail=email;
        const draftSaved=isOwner?true:saveApplicationDraft({email,name:raw('name').trim(),qualifications:raw('qualifications'),
            experience_years:Number(raw('experience_years')),achievements:raw('achievements'),specialisms:raw('specialisms'),
            photo_base64:draftPhoto});
        openDialog('Confirm your email',`<p>We have sent a confirmation link to <strong>${esc(email)}</strong>. Check your spam folder if it does not arrive.</p>`+(isOwner
          ?'<p>Open the email in your mailbox and click the link. You will be taken to the sign-in page; enter your password there to open the owner dashboard.</p>'
          :'<p><strong>Your application is not sent until you confirm.</strong> Click the link in the email.</p>'
           +'<p class="small muted">Opened in this same browser, your application is sent automatically. If the link opens somewhere else — for example in your phone’s browser instead of this app, which is normal — '
           +(claim?'your application is already saved and is sent automatically when you confirm; you will only be asked to add your photo.'
            :accountApplication?'your details are carried over: add your photo and press <em>Send application for approval</em> on that page.'
            :(draftSaved?'you will be asked to fill in the form again and press <em>Send application for approval</em>.'
              :'this browser could not keep your form, so you will be asked to fill it in again and press <em>Send application for approval</em>.'))
           +' Then come back here and sign in once you are approved.</p>'));
        return;
      }
      let session=sessionData.session;
      if(email===OWNER_EMAIL&&!sessionUsedPassword(session)){
        // Owner returning from the confirmation link: sign in with the password to get a session the server accepts.
        // A practitioner's application needs no password here; the confirmed email session is enough to send it.
        if(!raw('password'))throw Error('Your email is confirmed. Enter your password and press the button again to continue.');
        const {data:signedIn,error:signInError}=await sb.auth.signInWithPassword({email,password:raw('password')});
        if(signInError)throw Error(signInError.message);
        session=signedIn.session;
      }
      const {data:userData,error:userError}=await sb.auth.getUser();
      if(userError||userData?.user?.email?.toLowerCase()!==email||!userData.user.email_confirmed_at)
        throw Error('Confirm this professional email in Supabase before submitting the application');
      supabase_access_token=session.access_token;
      if(email===OWNER_EMAIL){
        // Verified owner: no application is needed; sign straight in to the owner dashboard.
        state.mode='studio';navigateSection('studio','/studio');
        await handleSupabaseLogin();return;
      }
    }
    const upload=form.elements.namedItem('photo').files?.[0];if(!upload)throw Error('Profile photo is required');
    const photo_base64=await profilePhotoBase64(upload);
    const plan=state.freeAccess?'free':(form.querySelector('input[name=plan]:checked')?.value||'core');
    const result=await api('/api/public/practitioner-registration',{method:'POST',body:{
        name:raw('name'),email:raw('email'),password:state.supabaseAuth?null:raw('password'),
        supabase_access_token,qualifications:raw('qualifications'),
        experience_years:Number(raw('experience_years')),achievements:raw('achievements'),specialisms:raw('specialisms'),
        consultation_fee_paise:state.freeAccess?0:Math.round(Number(raw('consultation_fee_rupees'))*100),plan,photo_base64,
        terms_accepted:form.elements.namedItem('terms_accepted').checked
    }});
    if(state.freeAccess){
      // The application is in; end the email session and show the sign-in page with the same message as the automatic path.
      if(state.supabaseAuth)await finishApplication(await requireSupabase());
      navigateSection('studio','/studio');render();
      notify('Your account is pending verification by the platform admin. Login and directory access remain disabled.');
      return;
    }
    state.registrationToken=result.registration_token;state.registrationPlan=result.plan;
    state.registrationStatus='Registration saved; public listing and workspace are locked until verified activation.';
    render();window.scrollTo({top:0,behavior:'smooth'});return;
 }
 if(type==='login'){
   if(state.supabaseAuth){
     const sb=await requireSupabase();
     const {error}=await sb.auth.signInWithPassword({email:raw('email'),password:raw('password')});
     if(error)throw Error(error.message);
     await handleSupabaseLogin();return;
   }
   const result=await api('/api/auth/login',{method:'POST',body:{email:raw('email'),password:raw('password'),terms_accepted:form.elements.namedItem('terms_accepted').checked}});
   clearPrivateState();state.user=result;state.csrf=result.csrf;if(window.SPPSPush)SPPSPush.start({scope:'workspace',csrf:state.csrf});await loadAll();state.view='overview';render();notify('Welcome to the practitioner workspace');return;
 }
 if(type==='athlete'){
   const created=await api('/api/athletes',{method:'POST',body:{code:raw('code'),display_name:raw('display_name'),sport:raw('sport'),athlete_group:raw('athlete_group'),service_consent_recorded:form.elements.namedItem('service_consent_recorded').checked}});state.athleteId=created.id;await loadAll();state.view='athletes';closeDialog();render();notify('Athlete created. Overview opened below.');if(window.matchMedia('(max-width:900px)').matches)document.getElementById('athlete-overview-panel')?.scrollIntoView({block:'start',behavior:'smooth'});return;
 }
 if(type==='case'){
   const r=await api('/api/cases',{method:'POST',body:{athlete_id:Number(raw('athlete_id')),title:raw('title'),category:raw('category'),goal:raw('goal'),next_review:raw('next_review')||null}});state.caseId=r.id;await loadAthlete(state.athleteId);closeDialog();render();notify('Case opened');return;
 }
 if(type==='note'){
   await api('/api/notes',{method:'POST',body:{case_id:Number(raw('case_id')),note_date:raw('note_date'),content:raw('content')}});await refreshCase();closeDialog();render();notify('Private draft saved');return;
 }
 if(type==='factor'){
   await api('/api/factors',{method:'POST',body:{label:raw('label'),group_name:raw('group_name'),kind:raw('kind'),unit:raw('unit'),low:Number(raw('low')),high:Number(raw('high')),higher_is:raw('higher_is')}});state.factors=await api('/api/factors');closeDialog();render();notify('Factor added');return;
 }
 if(type==='observation'){
   const athleteId=Number(raw('athlete_id'));if(state.athleteLoading||athleteId!==state.athleteId||state.athleteDetail?.athlete?.id!==athleteId||Number(raw('case_id')||0)!==Number(state.caseId||0))throw Error('Athlete or case changed. Reopen the monitoring form.');const saved=await api('/api/observations',{method:'POST',body:{athlete_id:athleteId,case_id:raw('case_id')?Number(raw('case_id')):null,factor_id:Number(raw('factor_id')),value:Number(raw('value')),observed_on:raw('observed_on'),context:raw('context')}});await verifyClinicalWrite('monitoring',athleteId,saved);return;
 }
 if(type==='assignment'){
   await api('/api/assignments',{method:'POST',body:{athlete_id:Number(raw('athlete_id')),case_id:Number(raw('case_id')),intervention_id:Number(raw('intervention_id')),dosage:raw('dosage')}});await refreshCase();closeDialog();render();notify('Intervention assigned');return;
 }
 if(type==='report'){
   await api('/api/reports',{method:'POST',body:{athlete_id:Number(raw('athlete_id')),case_id:Number(raw('case_id')),title:raw('title'),audience:raw('audience'),body:raw('body')}});await loadReports();state.view='reports';closeDialog();render();notify('Private report draft saved');return;
 }
}

// The server is authoritative. Refresh after returning to either client, and
// periodically while visible. Never replace an open form or queue offline writes.
// SPPS-ANDROID-001: an open Client bookings dialog refreshes its table in place (status changes
// made by the client, new requests, the video join window). Never while the practitioner has
// chosen a reschedule time or is focused on a control; the Add availability form is outside the
// refreshed region, so typed values are preserved.
const CLIENT_BOOKINGS_EMPTY='<tr><td colspan="6" class="muted">No appointment requests yet.</td></tr>';
let liveRefreshing=false;
function liveDialogBusy(live){
 const a=document.activeElement;
 return [...live.querySelectorAll('select')].some(x=>x.value)||!!(a&&live.contains(a)&&a.matches('select,input,textarea'));
}
async function refreshLiveDialog(){
 const live=modal.querySelector('[data-live="client-bookings"]');
 if(!live||liveRefreshing||!state.user||document.visibilityState!=='visible'||liveDialogBusy(live))return;
 liveRefreshing=true;
 try{
  const [rows,slots]=await Promise.all([api('/api/booking-requests'),api('/api/slots')]);
  if(!live.isConnected||liveDialogBusy(live))return;
  state.appointments=rows;const {open,body}=clientBookingRows(rows,slots);
  const tbody=live.querySelector('[data-live-body]'),html=body||CLIENT_BOOKINGS_EMPTY;
  if(tbody&&tbody.dataset.liveHtml!==html){tbody.innerHTML=html;tbody.dataset.liveHtml=html}
  const count=modal.querySelector('[data-live-open]');if(count)count.textContent=String(open.length);
 }catch(_){/* keep the last view; the next refresh retries */}finally{liveRefreshing=false}
}
setInterval(refreshLiveDialog,15000);
setInterval(()=>{
 for(const el of document.querySelectorAll('[data-appointment-video]')){
  const b=(state.appointments||[]).find(x=>String(x.booking_id)===el.dataset.appointmentVideo);
  if(b){const html=appointmentVideo(b);if(el.innerHTML!==html)el.innerHTML=html}
 }
},1000);

async function syncWorkspace(){
 if(modal.querySelector('[data-live]')){refreshLiveDialog();return}
 if(app.querySelector('[data-appointments]')&&liveDialogBusy(app.querySelector('[data-appointments]')))return;
 if(refreshing||!state.user||state.mode!=='studio'||document.visibilityState!=='visible'||modal.innerHTML)return;
 refreshing=true;
 try{await loadAll();if(!modal.innerHTML)render()}catch(err){if(state.user)notify('Could not sync. Check your connection and refresh.')}finally{refreshing=false}
}
window.addEventListener('focus',syncWorkspace);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')syncWorkspace()});
setInterval(syncWorkspace,30000);

// Lightweight in-app alert while the practitioner workspace is open.
// Not a push/email service; no polling occurs on hidden pages or when logged out.
setInterval(async()=>{
 if(!state.user||state.mode!=='studio'||document.visibilityState!=='visible')return;
 try{
  const latest=await api('/api/bookings');
  const known=new Set((state.bookings||[]).map(b=>b.id));
  const incoming=latest.filter(b=>!known.has(b.id)&&['awaiting_payment','paid_pending_review'].includes(b.status));
  state.bookings=latest;
  if(state.user?.is_admin){
   const previous=new Set((state.organisationQuotes||[]).map(q=>q.id));
   const updated=await api('/api/admin/organisation-quotes');
   const newer=updated.filter(q=>!previous.has(q.id));
   state.organisationQuotes=updated;
   if(newer.length){notify(`${newer.length} new institutional quotation enquiry received. Review Account, Privacy & Plans.`);if(state.view==='settings')render()}
  }
  if(incoming.length){notify(`${incoming.length} new booking request${incoming.length===1?'':'s'} received. Review Bookings & Revenue.`);if(['overview','bookings'].includes(state.view))render()}
 }catch{/* Session expiration is handled in api; transient connectivity is non-fatal. */}
},30000);
document.addEventListener('click',async e=>{const el=e.target.closest('[data-action]');if(!el)return;if(el.tagName==='A')e.preventDefault();try{await action(el,e)}catch(err){notify(err.message)}});
document.addEventListener('change',async e=>{if(e.target?.dataset?.action==='chart-switch'){state.chartType=e.target.value;render();return}if(e.target?.dataset?.action==='athlete-select'){if(!e.target.value)return;try{workspaceNavigation({athleteId:Number(e.target.value),caseId:null});await loadAthlete(Number(e.target.value),true);render()}catch(err){notify(err.message)}}if(e.target?.dataset?.action==='case-select'){workspaceNavigation({caseId:e.target.value?Number(e.target.value):null});if(!e.target.value){state.caseId=null;state.caseDetail=null;render();return}try{state.caseId=Number(e.target.value);await refreshCase();render()}catch(err){notify(err.message)}}});
document.addEventListener('submit',async e=>{const form=e.target.closest('form[data-form]');if(!form)return;e.preventDefault();if(form.dataset.submitting==='true')return;form.dataset.submitting='true';const submitter=e.submitter||form.querySelector('[type=submit]');const originalText=submitter?.textContent;const wasDisabled=submitter?.disabled??false;if(submitter){submitter.disabled=true;submitter.textContent='Saving…'}try{await submit(form)}catch(err){notify(err.message)}finally{delete form.dataset.submitting;if(submitter){submitter.disabled=wasDisabled;submitter.textContent=originalText}}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal.innerHTML)closeDialog()});
window.addEventListener('popstate',event=>{void restoreNavigationScreen(event)});
// v2 screens (web/v2.js) use these helpers; nothing else is exposed.
window.SPPSHost=Object.freeze({state,api,render,pageHead,button,badge,esc,when,openDialog,closeDialog,notify,loadAll});
(async()=>{try{await initSupabaseAuth();{const runtime=await api('/api/runtime');integrationDevelopment=runtime.integration_development;state.clinicalParity=Boolean(runtime.clinical_parity);state.portalEnabled=Boolean(runtime.portal);state.v2flags=runtime.v2_flags||{};state.freeAccess=true;state.backendFreeAccess=Boolean(runtime.free_access);}if(integrationDevelopment && state.mode==='studio'){await openStudio();return}const safe=(path,fallback)=>api(path).catch(()=>fallback);try{state.formToken=(await api('/api/public/form-token')).form_token}catch{}const [s,p,plans,programmes]=await Promise.all([safe('/api/public/services',{services:[]}),safe('/api/public/practitioners',[]),safe('/api/public/practitioner-plans',null),safe('/api/public/programmes',[])]);state.services=s.services;state.practitioners=p;state.publicPlans=plans;state.publicProgrammes=programmes;
if(state.supabaseAuth&&(state.supabaseEmail||'').toLowerCase()===OWNER_EMAIL&&!state.user&&['/register','/studio'].includes(location.pathname)){try{state.mode='studio';navigateSection('studio','/studio');await handleSupabaseLogin();if(state.user){render();return}}catch(err){if(err.message===LINK_SESSION_MESSAGE)state.loginNotice={title:'Email confirmed',text:'Enter your password below to open the owner dashboard.'};notify(err.message)}}
else if(state.supabaseAuth&&state.supabaseEmail&&state.emailVerifiedNotice&&!state.user){
 // v2: promote the server-side application first; this works in any browser and inside the Android app.
 let promoted=null;try{promoted=await promoteServerApplication()}catch{promoted=null}
 if(promoted&&promoted.has_application&&promoted.practitioner_id){
  clearApplicationDraft();
  if(promoted.needs_photo){state.photoAfterConfirm=true;state.emailVerifiedNotice=false;render()}
  else{const sb=await requireSupabase();await finishApplication(sb);state.mode='studio';navigateSection('studio','/studio')}
 }
 // An applicant who has just clicked the confirmation link: send the saved application, then show the sign-in page.
 const draft=promoted&&promoted.has_application?null:loadApplicationDraft(state.supabaseEmail);
 if(draft){try{await submitSavedApplication(draft)}catch(err){notify(`Your email is confirmed, but the application could not be sent: ${err.message}. Press Send application for approval to try again.`)}}
}
if(state.mode==='studio'){await openStudio();if(state.view==='admin'&&state.user?.is_admin){state.adminReviews=await api('/api/admin/practitioner-reviews');render()}}else render()}catch(err){state.freeAccess=true;render();notify('Public preview loaded; practitioner services are not yet available.')}})();
})();
