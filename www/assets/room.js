/* SPPS Version 1.0 - Free: private consent-gated peer-to-peer consultation.
 * Signals contain SDP/ICE only. No audio/video recording or telemetry.
 * Invitation tokens, ICE candidates, IP addresses, SDP and clinical content
 * MUST NEVER be placed into the on-screen/copyable diagnostics.
 */
(()=>{'use strict';
const el=id=>document.getElementById(id);
const status=el('room-status'),diagnostics=el('room-diagnostics');
const panel=el('room-consent'),call=el('room-call');
const platformVideo=/^\/video\/\d+$/.test(location.pathname);
const m=location.pathname.match(/^\/(?:room|video)\/(\d+)$/);
const rid=Number(m?.[1]);
const credential=platformVideo?'ticket':'access';
const token=new URLSearchParams(location.search).get(credential)||'';
// Drop the bearer from the address bar/history once read (it stays in memory only).
if(token&&typeof history!=='undefined'&&history.replaceState)history.replaceState({},'',location.pathname);
const local=el('local-video'),remote=el('remote-video');
const playButton=el('play-remote'),startButton=el('start-camera');
let role='',ws=null,peer=null,stream=null;
let pendingRemoteIce=[],pendingLocalIce=[],closing=false,offering=false,statsTimer=null;
// SPPS-VIDEO-001: bounded ICE recovery + address-free candidate diagnostics.
const MAX_RESTARTS=2,GRACE_MS=8000;
let restarts=0,recovered=false,graceTimer=null,localTypes={},remoteTypes={};
const CANDIDATE_TYPES=['host','srflx','prflx','relay'];
const candidateType=c=>(c&&CANDIDATE_TYPES.includes(c.type)&&c.type)||(String(c?.candidate||'').match(/ typ (host|srflx|prflx|relay)(?: |$)/)||[])[1]||'';
const typeCounts=o=>CANDIDATE_TYPES.filter(k=>o[k]).map(k=>`${k} ${o[k]}`).join(', ')||'None';
const phases={
 invitation:'Checking',consent:'Pending',local:'Not started',signaling:'Not connected',
 participant:'Waiting',negotiation:'Not started',transport:'Not configured',
 gathering:'Not started',candidates:'None',remoteCandidates:'None',
 ice:'Not started',selectedPair:'None',recovery:'Not needed',peer:'Not started',remoteTrack:'Waiting',videoFrames:'None decoded',
 inboundVideo:'No packets received',inboundAudio:'No packets received',playback:'Not started'
};
const phaseNames=Object.keys(phases);
// The room is a separate document: restore the same three themes as the workspace.
try {
 const stored=localStorage.getItem('modelc-theme');
 if(['original','light','dark'].includes(stored))document.body.dataset.theme=stored;
} catch (_) { /* Private browsing may restrict preference storage. */ }
const say=text=>{status.textContent=text};
function renderPhases(){
 for(const name of phaseNames){const field=el('phase-'+name);if(field)field.textContent=phases[name]}
 if(diagnostics)diagnostics.textContent=`Media connection: ICE ${phases.ice}; decoded frames ${phases.videoFrames}; playback ${phases.playback}.`;
}
function phase(name,value){
 if(!Object.hasOwn(phases,name))return;
 // All phase values in this client are fixed diagnostic labels, not server payloads.
 phases[name]=value;renderPhases();
}
function diagnosticSummary(){
 return 'SPPS Version 1.0 - Free — redacted media diagnostics (no invitation or network addresses)\n'+
  phaseNames.map(key=>`${key}: ${phases[key]}`).join('\n')+
  '\nNote: audible two-way audio must be verified by the participants.';
}
el('copy-diagnostics').onclick=async()=>{
 const text=diagnosticSummary();
 try{await navigator.clipboard.writeText(text);say('Safe diagnostic states copied; no token or candidate details included.')}
 catch{window.prompt('Copy these redacted diagnostics',text)}
};
const fetchJson=async(path,opts={})=>{
 const response=await fetch(path,{credentials:'omit',cache:'no-store',...opts});
 const data=await response.json().catch(()=>({}));
 if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:`HTTP ${response.status}`);
 return data;
};
const api=(endpoint)=>`/api/${platformVideo?'video':'room'}/${rid}/${endpoint}?${credential}=${encodeURIComponent(token)}`;
const socketAddress=()=>`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/${platformVideo?'video':'room'}/${rid}?${credential}=${encodeURIComponent(token)}`;
const send=message=>{
 if(ws?.readyState===WebSocket.OPEN){ws.send(JSON.stringify(message));return true}
 return false;
};
const flushLocalIce=()=>{
 while(pendingLocalIce.length&&ws?.readyState===WebSocket.OPEN){send({type:'ice',candidate:pendingLocalIce.shift()})}
};
async function flushRemoteIce(){
 if(!peer?.remoteDescription)return;
 while(pendingRemoteIce.length)await peer.addIceCandidate(pendingRemoteIce.shift());
}
function stopStats(){if(statsTimer){clearInterval(statsTimer);statsTimer=null}}
async function updateInboundStats(){
 const active=peer;
 if(!active||active.signalingState==='closed')return;
 try{
  const reports=await active.getStats();
  if(peer!==active)return;
  let vb=0,vf=0,ab=0,pair=null;
  reports.forEach(s=>{if(s.type==='transport'&&s.selectedCandidatePairId)pair=reports.get(s.selectedCandidatePairId)||pair});
  if(!pair)reports.forEach(s=>{if(s.type==='candidate-pair'&&s.nominated&&s.state==='succeeded')pair=s});
  const lc=pair&&reports.get(pair.localCandidateId),rc=pair&&reports.get(pair.remoteCandidateId);
  if(lc&&rc){
   const side=c=>`${CANDIDATE_TYPES.includes(c.candidateType)?c.candidateType:'unknown'}/${c.protocol==='tcp'?'tcp':'udp'}`;
   phase('selectedPair',`${side(lc)}${lc.relayProtocol?` via TURN ${['udp','tcp','tls'].includes(lc.relayProtocol)?lc.relayProtocol:'?'}`:''} ↔ ${side(rc)}`);
  }
  reports.forEach(s=>{
   if(s.type!=='inbound-rtp'||s.isRemote)return;
   const kind=s.kind||s.mediaType;
   if(kind==='video'){vb+=Number(s.bytesReceived||0);vf+=Number(s.framesDecoded||0)}
   else if(kind==='audio')ab+=Number(s.bytesReceived||0);
  });
  phase('inboundVideo',vb>0?'Inbound RTP received':'No packets received');
  phase('videoFrames',vf>0?`${vf} decoded (cumulative)`:'None decoded');
  phase('inboundAudio',ab>0?'Inbound RTP received; listen to confirm':'No packets received');
 }catch(_){phase('inboundVideo','Stats unavailable')}
}
function startStats(){stopStats();statsTimer=setInterval(updateInboundStats,2500);updateInboundStats()}
function clearGrace(){if(graceTimer){clearTimeout(graceTimer);graceTimer=null}}
function closeMedia(){
 closing=true;stopStats();offering=false;clearGrace();
 restarts=0;recovered=false;localTypes={};remoteTypes={};
 if(ws){const sock=ws;ws=null;sock.onclose=null;sock.onerror=null;sock.onmessage=null;sock.close(1000,'User left')}
 if(peer){peer.ontrack=null;peer.onicecandidate=null;peer.oniceconnectionstatechange=null;peer.close();peer=null}
 if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
 local.srcObject=null;remote.srcObject=null;
 pendingRemoteIce=[];pendingLocalIce=[];
 startButton.disabled=false;playButton.hidden=true;
 phase('local','Stopped');phase('signaling','Disconnected');phase('participant','Waiting');
 phase('ice','Stopped');phase('peer','Stopped');phase('remoteTrack','Stopped');
 phase('videoFrames','None decoded');phase('inboundAudio','No packets received');
 phase('inboundVideo','No packets received');phase('playback','Stopped');
 phase('gathering','Stopped');phase('candidates','None');phase('remoteCandidates','None');
 phase('selectedPair','None');phase('recovery','Not needed');
}
async function verifyInvitation(){
 if(!rid||!token){phase('invitation','Missing');say('Invitation missing. Request a private link from the practitioner.');return}
 try{
  const result=await fetchJson(api('status'));
  role=result.role;phase('invitation',role==='practitioner'?'Verified: practitioner':'Verified: guest');
  phase('consent',result.consented?'Accepted':'Required');
  panel.hidden=!!result.consented;call.hidden=!result.consented;
  say(result.consented?'Invitation accepted. Start camera to join.':'Invitation verified. Review and confirm video consent.');
 }catch(error){phase('invitation','Rejected or expired');say('Cannot access consultation room: '+error.message)}
}
el('accept-consent').onclick=async()=>{
 if(!el('video-optin').checked){say('Video consent is required before joining.');return}
 try{
  await fetchJson(api('consent'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({accepted:true})});
  phase('consent','Accepted');panel.hidden=true;call.hidden=false;say('Consent recorded. Start the camera when ready.');
 }catch(error){say(error.message)}
};
async function makeOffer(options={}){
 if(!peer||peer.signalingState!=='stable'||offering)return;
 offering=true;phase('negotiation',options.iceRestart?'Creating ICE restart offer':'Creating practitioner offer');
 try{
  const offer=await peer.createOffer(options);
  await peer.setLocalDescription(offer);
  if(!send({type:'offer',sdp:peer.localDescription}))throw Error('Signaling connection not open');
  phase('negotiation','Offer sent; waiting for answer');
 }finally{offering=false}
}
async function receive(message){
 if(message.type==='ready'){
  phase('signaling','Connected');phase('participant','Waiting for peer');
  say(role==='practitioner'?'You are in the room. Your client can join now; the call starts when they do.':'You are in the waiting room. Your practitioner will let the call start when they join. Please keep this page open.');
 }else if(message.type==='peer-ready'){
  phase('participant','Both connected');
  say(role==='practitioner'?'Your client has joined. Connecting the call.':'Your practitioner has joined. Connecting the call.');
  phase('negotiation','Negotiating media');
  if(role==='practitioner')await makeOffer();
 }else if(message.type==='peer-left'){
  closeMedia();
  say('Other participant left. Both people should press Start camera again. Start camera to retry.');
  phase('participant','Peer left; manual rejoin available');
 }else if(message.type==='offer'){
  if(!peer)return;
  phase('negotiation','Guest received offer');
  await peer.setRemoteDescription(message.sdp);
  await flushRemoteIce();
  const answer=await peer.createAnswer();
  await peer.setLocalDescription(answer);
  if(!send({type:'answer',sdp:peer.localDescription}))throw Error('Signaling disconnected during answer');
  phase('negotiation','Answer sent');
 }else if(message.type==='answer'){
  if(!peer)return;
  await peer.setRemoteDescription(message.sdp);
  await flushRemoteIce();
  phase('negotiation','Offer and answer exchanged');
 }else if(message.type==='ice'&&message.candidate){
  if(!peer)return;
  const kind=candidateType(message.candidate);
  if(kind){remoteTypes[kind]=(remoteTypes[kind]||0)+1;phase('remoteCandidates',typeCounts(remoteTypes))}
  if(peer.remoteDescription){await peer.addIceCandidate(message.candidate)}
  else pendingRemoteIce.push(message.candidate);
 }
}
// Only the practitioner (offerer) restarts ICE; the guest answers the restart offer as usual.
function restartIce(reason){
 if(!peer||closing)return;
 if(role!=='practitioner'){phase('recovery',`${reason}; waiting for practitioner ICE restart`);return}
 if(restarts>=MAX_RESTARTS){
  phase('recovery','ICE restart limit reached; press Start camera to retry');
  say('ICE connection failed. Check Wi-Fi, firewall and configured STUN/TURN. Copy diagnostics for troubleshooting.');
  return;
 }
 restarts+=1;phase('recovery',`${reason}; ICE restart ${restarts}/${MAX_RESTARTS}`);
 makeOffer({iceRestart:true}).catch(()=>phase('recovery','ICE restart could not be sent'));
}
function onIceState(){
 if(!peer||closing)return;
 const state=peer.iceConnectionState;
 phase('ice',state);
 if(state==='connected'||state==='completed'){
  clearGrace();
  if(restarts){recovered=true;restarts=0}
  phase('recovery',recovered?'Recovered after ICE restart':'Not needed');
 }else if(state==='disconnected'){
  clearGrace();
  phase('recovery',`Disconnected; waiting ${GRACE_MS/1000} s before ICE restart`);
  graceTimer=setTimeout(()=>{graceTimer=null;if(peer&&['disconnected','failed'].includes(peer.iceConnectionState))restartIce('Still disconnected')},GRACE_MS);
 }else if(state==='failed'){
  clearGrace();
  say('ICE connection failed; attempting recovery. Copy diagnostics if it does not reconnect.');
  restartIce('ICE failed');
 }
}
function transportKind(config){
 const types=(config.iceServers||[]).flatMap(x=>Array.isArray(x.urls)?x.urls:[x.urls]).filter(Boolean).map(x=>String(x).split(':')[0].toLowerCase());
 const relayOnly=config.iceTransportPolicy==='relay'?' (relay only)':'';
 return types.includes('turn')||types.includes('turns')?'TURN available'+relayOnly:types.includes('stun')?'STUN only':'No STUN/TURN configured';
}
function playRemote(){
 if(!remote.srcObject)return;
 try{
  const playback=remote.play();
  if(playback?.then){playback.then(()=>{phase('playback','Started; verify audio manually');playButton.hidden=true})
    .catch(()=>{phase('playback','Blocked — press Play remote media');playButton.hidden=false})}
  else{phase('playback','Start requested; verify playback')}
 }catch(_){phase('playback','Blocked — press Play remote media');playButton.hidden=false}
}
playButton.onclick=playRemote;
remote.onclick=playRemote;
remote.addEventListener('playing',()=>{if(remote.srcObject)phase('playback','Playing; verify audio manually')});
remote.addEventListener('error',()=>{if(remote.srcObject)phase('playback','Media element error')});
remote.addEventListener('loadeddata',()=>{if(remote.srcObject)phase('playback','Remote video element has data')});
startButton.onclick=async()=>{
 if(!role){say('Verify room invitation first.');return}
 if(startButton.disabled)return;
 startButton.disabled=true;
 try{
  closing=false;
  if(!window.isSecureContext)throw Error('HTTPS or localhost required for camera and microphone');
  if(!navigator.mediaDevices?.getUserMedia)throw Error('Camera/microphone API unavailable in this WebView');
  stream=await navigator.mediaDevices.getUserMedia({video:true,audio:true});
  local.srcObject=stream;
  phase('local',`Camera ${stream.getVideoTracks().length?'ready':'missing'}, microphone ${stream.getAudioTracks().length?'ready':'missing'}`);
  const cfg=await fetchJson(api('ice'));
  phase('transport',transportKind(cfg));
  peer=new RTCPeerConnection({iceServers:cfg.iceServers||[],iceTransportPolicy:cfg.iceTransportPolicy==='relay'?'relay':'all'});
  stream.getTracks().forEach(track=>peer.addTrack(track,stream));
  peer.ontrack=event=>{
   if(!peer)return;
   remote.srcObject=event.streams[0]||new MediaStream([event.track]);
   phase('remoteTrack','Received; frames not yet verified');
   playRemote();
  };
  peer.onicecandidate=event=>{
   if(!event.candidate){if(peer&&!closing)phase('gathering','Complete');return}
   const kind=candidateType(event.candidate);
   if(kind){localTypes[kind]=(localTypes[kind]||0)+1;phase('candidates',typeCounts(localTypes))}
   if(!send({type:'ice',candidate:event.candidate}))pendingLocalIce.push(event.candidate);
  };
  peer.onconnectionstatechange=()=>{
   if(peer&&!closing)phase('peer',peer.connectionState);
  };
  peer.oniceconnectionstatechange=onIceState;
  peer.onicegatheringstatechange=()=>{
   if(!peer||closing)return;
   phase('gathering',peer.iceGatheringState==='complete'?'Complete':peer.iceGatheringState==='gathering'?'Gathering':'New');
   if(peer.iceGatheringState==='complete'&&phases.ice==='Not started')phase('ice','Gathering complete; awaiting connection');
  };
  ws=new WebSocket(socketAddress());
  ws.onopen=()=>{phase('signaling','Connected');flushLocalIce();say('Signaling connected. Waiting for the other participant.')};
  ws.onmessage=event=>{
   try{
    const message=JSON.parse(event.data);
    Promise.resolve(receive(message)).catch(error=>{phase('negotiation','Error; retry required');say('WebRTC negotiation problem: '+error.message)});
   }catch(error){phase('negotiation','Invalid signaling message');say('Unexpected signaling message: '+error.message)}
  };
  ws.onerror=()=>{if(!closing){phase('signaling','Error');say('WebSocket signaling failed. Verify server WebSocket support and both invitations.')}};
  ws.onclose=event=>{
   if(!closing){closeMedia();phase('signaling',`Closed (code ${Number(event.code)||0})`);
    say(`Room signaling disconnected (code ${event.code}). Check the backend and invitation validity. Start camera to retry.`)}
  };
  startStats();
 }catch(error){say('Camera or connection unavailable: '+error.message);phase('local','Permission or setup failure');closeMedia();phase('local','Permission or setup failure')}
};
el('hangup').onclick=()=>{closeMedia();say('Call ended. Camera and microphone stopped. This does not mark the consultation completed.')};
window.addEventListener('pagehide',closeMedia);
renderPhases();verifyInvitation();
})();
