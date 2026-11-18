/* Simulates stage transitions only; does NOT substitute for real browser media testing. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const js=readFileSync('web/room.js','utf8');
function harness(){
 const fields=new Map();
 const element=id=>{
  if(!fields.has(id))fields.set(id,{id,textContent:'',hidden:false,disabled:false,checked:true,srcObject:null,addEventListener(){},play(){return Promise.resolve()}});
  return fields.get(id);
 };
 const documents={getElementById:element,body:{dataset:{}}};
 const tracks=[{kind:'video',stop(){this.stopped=true}},{kind:'audio',stop(){this.stopped=true}}];
 const stream={getTracks:()=>tracks,getVideoTracks:()=>[tracks[0]],getAudioTracks:()=>[tracks[1]]};
 const sockets=[];const peers=[];let copy='',statsPoll=null;
 class Socket {
  static OPEN=1;
  constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}
  send(msg){this.sent.push(JSON.parse(msg))}
  close(){this.readyState=3}
 }
 class Peer {
  constructor(cfg){this.cfg=cfg;this.signalingState='stable';this.remoteDescription=null;this.iceConnectionState='new';this.connectionState='new';peers.push(this)}
  addTrack(){};
  createOffer(){return Promise.resolve({type:'offer',sdp:'synthetic sdp not copied'})}
  setLocalDescription(desc){this.localDescription=desc;return Promise.resolve()}
  setRemoteDescription(desc){this.remoteDescription=desc;return Promise.resolve()}
  createAnswer(){return Promise.resolve({type:'answer',sdp:'synthetic'})}
  addIceCandidate(){return Promise.resolve()}
  getStats(){return Promise.resolve(new Map([['v',{type:'inbound-rtp',kind:'video',bytesReceived:1200,framesDecoded:7}],['a',{type:'inbound-rtp',kind:'audio',bytesReceived:900}]]))}
  close(){this.signalingState='closed'}
 }
 const request=async path=>{
  const payload=path.includes('/status?')?{role:'practitioner',consented:true}:{iceServers:[{urls:'stun:stun.example.org:3478'}]};
  return {ok:true,json:async()=>payload};
 };
 const ctx={document:documents,location:{pathname:'/room/4',search:'?access=TOP_SECRET_BEARER',protocol:'http:',host:'localhost:8000'},
  localStorage:{getItem:()=> 'dark'},window:{isSecureContext:true,addEventListener(){},prompt(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>stream},clipboard:{writeText:async text=>{copy=text}}},
  URLSearchParams,fetch:request,WebSocket:Socket,RTCPeerConnection:Peer,
  MediaStream:class{},setInterval:fn=>{statsPoll=fn;return 1},clearInterval:()=>{statsPoll=null},
 };
 vm.runInNewContext(js,ctx,{timeout:1000});
 return {fields,element,sockets,peers,tracks,get copy(){return copy},get statsPoll(){return statsPoll}};
}
const drain=()=>new Promise(resolve=>setImmediate(resolve));
test('room diagnostics cover ICE and actual inbound decoded frames, without copying bearer token',async()=>{
 const h=harness();await drain();
 assert.equal(h.element('phase-invitation').textContent,'Verified: practitioner');
 await h.element('start-camera').onclick();await drain();
 assert.equal(h.element('phase-local').textContent,'Camera ready, microphone ready');
 assert.equal(h.element('phase-transport').textContent,'STUN only');
 const ws=h.sockets[0];ws.onopen();ws.onmessage({data:JSON.stringify({type:'peer-ready'})});await drain();
 assert(ws.sent.some(item=>item.type==='offer'));
 h.peers[0].oniceconnectionstatechange();
 h.peers[0].ontrack({streams:[{}],track:{kind:'video'}});
 await h.statsPoll();await drain();
 assert.equal(h.element('phase-videoFrames').textContent,'7 decoded (cumulative)');
 assert.match(h.element('phase-inboundAudio').textContent,/Inbound RTP received/);
 await h.element('copy-diagnostics').onclick();
 assert.match(h.copy,/videoFrames: 7 decoded/);
 assert.doesNotMatch(h.copy,/TOP_SECRET_BEARER|stun\.example\.org|synthetic sdp|localhost:8000/);
 h.element('hangup').onclick();
 assert(h.tracks.every(t=>t.stopped));
 assert.equal(h.element('start-camera').disabled,false);
});
