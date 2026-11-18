/* SPPS-VIDEO-001: relay policy, bounded ICE restart, address-free candidate diagnostics.
 * State-machine simulation only; real media is covered by the Playwright TURN relay test. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const js=readFileSync('web/room.js','utf8');
const ADDR='203.0.113.77', ADDR6='2001:db8::77';
function harness({role='practitioner',ice={iceServers:[{urls:['turn:127.0.0.1:3478?transport=tcp'],username:'1:x',credential:'SECRET_CRED'}],iceTransportPolicy:'relay'}}={}){
 const fields=new Map();
 const element=id=>{
  if(!fields.has(id))fields.set(id,{id,textContent:'',hidden:false,disabled:false,checked:true,srcObject:null,addEventListener(){},play(){return Promise.resolve()}});
  return fields.get(id);
 };
 const tracks=[{kind:'video',stop(){}},{kind:'audio',stop(){}}];
 const stream={getTracks:()=>tracks,getVideoTracks:()=>[tracks[0]],getAudioTracks:()=>[tracks[1]]};
 const sockets=[],peers=[],timers=[];let copy='',statsPoll=null;
 class Socket{static OPEN=1;constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}send(m){this.sent.push(JSON.parse(m))}close(){this.readyState=3}}
 class Peer{
  constructor(cfg){this.cfg=cfg;this.signalingState='stable';this.remoteDescription=null;this.iceConnectionState='new';this.offers=[];peers.push(this)}
  addTrack(){}
  createOffer(o){this.offers.push(o||{});return Promise.resolve({type:'offer',sdp:'v=0 c=IN IP4 '+ADDR})}
  setLocalDescription(d){this.localDescription=d;return Promise.resolve()}
  setRemoteDescription(d){this.remoteDescription=d;return Promise.resolve()}
  createAnswer(){return Promise.resolve({type:'answer',sdp:'synthetic'})}
  addIceCandidate(){return Promise.resolve()}
  getStats(){return Promise.resolve(new Map([
   ['T',{type:'transport',selectedCandidatePairId:'P'}],
   ['P',{type:'candidate-pair',localCandidateId:'L',remoteCandidateId:'R',state:'succeeded',nominated:true}],
   ['L',{type:'local-candidate',candidateType:'relay',protocol:'udp',relayProtocol:'tcp',address:ADDR,port:50000}],
   ['R',{type:'remote-candidate',candidateType:'relay',protocol:'udp',address:ADDR6,port:50001}],
   ['v',{type:'inbound-rtp',kind:'video',bytesReceived:10,framesDecoded:3}]]))}
  close(){this.signalingState='closed'}
 }
 const request=async path=>({ok:true,json:async()=>path.includes('/status?')?{role,consented:true}:ice});
 const ctx={document:{getElementById:element,body:{dataset:{}}},
  location:{pathname:'/video/9',search:'?ticket=TOP_SECRET_TICKET',protocol:'http:',host:'localhost:8010'},
  history:{replaceState(){}},localStorage:{getItem:()=>null},window:{isSecureContext:true,addEventListener(){},prompt(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>stream},clipboard:{writeText:async t=>{copy=t}}},
  URLSearchParams,fetch:request,WebSocket:Socket,RTCPeerConnection:Peer,MediaStream:class{},
  setInterval:fn=>{statsPoll=fn;return 1},clearInterval:()=>{statsPoll=null},
  setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearTimeout:id=>{if(timers[id-1])timers[id-1].fn=null}};
 vm.runInNewContext(js,ctx,{timeout:1000});
 return {element,sockets,peers,timers,get copy(){return copy},get statsPoll(){return statsPoll}};
}
const drain=()=>new Promise(r=>setImmediate(r));
async function joined(h){
 await drain();await h.element('start-camera').onclick();await drain();
 const ws=h.sockets[0];ws.onopen();ws.onmessage({data:JSON.stringify({type:'peer-ready'})});await drain();
 return {ws,peer:h.peers[0]};
}
const setIce=(peer,state)=>{peer.iceConnectionState=state;peer.oniceconnectionstatechange()};

test('relay-only policy from the server reaches RTCPeerConnection and is shown',async()=>{
 const h=harness();const {peer}=await joined(h);
 assert.equal(peer.cfg.iceTransportPolicy,'relay');
 assert.equal(h.element('phase-transport').textContent,'TURN available (relay only)');
 const host=harness({ice:{iceServers:[]}});const {peer:p2}=await joined(host);
 assert.equal(p2.cfg.iceTransportPolicy,'all');
});

test('failed ICE triggers at most two practitioner restart offers, then asks for a manual retry',async()=>{
 const h=harness();const {ws,peer}=await joined(h);
 assert.equal(peer.offers.length,1);assert.notEqual(peer.offers[0].iceRestart,true);
 for(let i=0;i<3;i++){setIce(peer,'failed');await drain()}
 assert.deepEqual(peer.offers.slice(1).map(o=>o.iceRestart),[true,true]);
 assert.equal(ws.sent.filter(m=>m.type==='offer').length,3);
 assert.match(h.element('phase-recovery').textContent,/limit reached/);
 setIce(peer,'connected');
 assert.equal(h.element('phase-recovery').textContent,'Recovered after ICE restart');
});

test('disconnected waits for the grace period and restarts only if still disconnected',async()=>{
 const h=harness();const {peer}=await joined(h);
 setIce(peer,'disconnected');
 assert.equal(h.timers.at(-1).ms,8000);
 setIce(peer,'connected');                    // transient blip: timer cancelled
 assert.equal(h.timers[0].fn,null);
 setIce(peer,'disconnected');h.timers.at(-1).fn();await drain();
 assert.equal(peer.offers.at(-1).iceRestart,true);
});

test('guest never offers; it waits for the practitioner restart',async()=>{
 const h=harness({role:'guest'});const {ws,peer}=await joined(h);
 setIce(peer,'failed');await drain();
 assert.equal(ws.sent.filter(m=>m.type==='offer').length,0);
 assert.match(h.element('phase-recovery').textContent,/waiting for practitioner/);
});

test('candidate and selected-path diagnostics contain types only, never addresses or credentials',async()=>{
 const h=harness();const {ws,peer}=await joined(h);
 peer.onicecandidate({candidate:{type:'relay',candidate:`candidate:1 1 udp 5 ${ADDR} 50000 typ relay raddr 0.0.0.0 rport 0`,toJSON(){return {}}}});
 peer.onicecandidate({candidate:{type:'host',candidate:`candidate:2 1 udp 9 ${ADDR6} 50002 typ host`,toJSON(){return {}}}});
 peer.onicecandidate({candidate:null});
 ws.onmessage({data:JSON.stringify({type:'ice',candidate:{candidate:`candidate:3 1 tcp 7 ${ADDR} 9 typ srflx raddr ${ADDR6} rport 1`}})});await drain();
 await h.statsPoll();await drain();
 assert.equal(h.element('phase-candidates').textContent,'host 1, relay 1');
 assert.equal(h.element('phase-remoteCandidates').textContent,'srflx 1');
 assert.equal(h.element('phase-gathering').textContent,'Complete');
 assert.equal(h.element('phase-selectedPair').textContent,'relay/udp via TURN tcp ↔ relay/udp');
 await h.element('copy-diagnostics').onclick();
 assert.match(h.copy,/selectedPair: relay\/udp via TURN tcp/);
 for(const leak of [ADDR,ADDR6,'SECRET_CRED','TOP_SECRET_TICKET','127.0.0.1','50000'])assert(!h.copy.includes(leak),leak);
});
