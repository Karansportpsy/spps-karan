"""Level D: redacted ICE probe inside the physical RC app's WebView (SPPS-VIDEO-001).

    python -m tools.webview_ice_probe            # host candidates + in-WebView loopback pair
    python -m tools.webview_ice_probe --turn     # also: can the WebView allocate a relay on the TURN bench?

Requirements: phone unlocked (never bypassed), SPPS RC Validation in the foreground, USB debugging.
Uses Chrome DevTools Protocol on the debug WebView: ONE Runtime.evaluate of a self-contained function
that creates throwaway RTCPeerConnections (no getUserMedia, no navigation, no DOM/storage writes) and
closes them. Only candidate TYPES, address CLASSES, network cost and pair types leave the device:
never IP addresses, ports, SDP, tokens or credentials. TURN credentials are derived, expire in 10 min
and are not printed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PACKAGE = "com.winmindperform.spps.beta15.rcvalidation"
FORWARD_PORT = 9333

PROBE = r"""
async (turn) => {
  const cls = (cand) => {
    const p = cand.split(' '); const addr = p[4] || ''; const out = {type: p[7], proto: (p[2] || '').toLowerCase()};
    const cost = cand.match(/network-cost (\d+)/); out.cost = cost ? Number(cost[1]) : null;
    const nid = cand.match(/network-id (\d+)/); out.nid = nid ? Number(nid[1]) : null;
    if (addr.endsWith('.local')) out.addr = 'mDNS-obfuscated';
    else if (addr.includes(':')) { const a = addr.toLowerCase(); out.addr = a.startsWith('fe80') ? 'IPv6 link-local' : /^f[cd]/.test(a) ? 'IPv6 ULA' : 'IPv6 global'; }
    else { const o = addr.split('.').map(Number);
      out.addr = (o[0] === 10 && o[1] === 231 && o[2] === 77) ? 'IPv4 TURN bench relay'
        : (o[0] === 10 && o[1] === 20 && o[2] === 14) ? 'IPv4 phone-hotspot subnet'
        : (o[0] === 192 && o[1] === 0 && o[2] === 0) ? 'IPv4 464XLAT CLAT'
        : (o[0] === 100 && o[1] >= 64 && o[1] < 128) ? 'IPv4 CGNAT'
        : (o[0] === 10 || (o[0] === 172 && o[1] >= 16 && o[1] < 32) || (o[0] === 192 && o[1] === 168)) ? 'IPv4 other private'
        : o[0] === 127 ? 'IPv4 loopback' : 'IPv4 global'; }
    return out;
  };
  const gather = async (cfg) => {
    const pc = new RTCPeerConnection(cfg); const states = [], cands = [];
    pc.onicegatheringstatechange = () => states.push(pc.iceGatheringState);
    pc.onicecandidate = e => { if (e.candidate && e.candidate.candidate) cands.push(cls(e.candidate.candidate)); };
    let errors = 0; pc.onicecandidateerror = () => { errors += 1; };
    pc.addTransceiver('audio'); pc.addTransceiver('video');
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r => { const t = setTimeout(r, 9000); const iv = setInterval(() => { if (pc.iceGatheringState === 'complete') { clearTimeout(t); clearInterval(iv); r(); } }, 100); });
    const summary = {};
    cands.forEach(c => { const k = `${c.type}/${c.proto} | ${c.addr} | network-cost ${c.cost}`; summary[k] = (summary[k] || 0) + 1; });
    const out = {gatheringStates: states, final: pc.iceGatheringState, candidates: summary,
                 distinctNetworks: new Set(cands.map(c => c.nid)).size, candidateErrors: errors};
    pc.close(); return out;
  };
  const result = {webview: (navigator.userAgent.match(/Chrome\/(\d+)/) || [])[1] || '?', secure: isSecureContext};
  result.hostOnly = await gather({iceServers: []});
  if (turn) result.turnRelayOnly = await gather({iceServers: [turn], iceTransportPolicy: 'relay'});
  // In-WebView loopback pair (host only): selected pair types only.
  const a = new RTCPeerConnection({iceServers: []}), b = new RTCPeerConnection({iceServers: []});
  a.onicecandidate = e => e.candidate && b.addIceCandidate(e.candidate).catch(() => {});
  b.onicecandidate = e => e.candidate && a.addIceCandidate(e.candidate).catch(() => {});
  const ch = a.createDataChannel('probe'); let opened = false; ch.onopen = () => { opened = true; };
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
  await new Promise(r => setTimeout(r, 6000));
  const stats = await a.getStats(); let sel = null;
  stats.forEach(s => { if (s.type === 'transport' && s.selectedCandidatePairId) sel = stats.get(s.selectedCandidatePairId); });
  const lc = sel && stats.get(sel.localCandidateId);
  result.loopback = {ice: a.iceConnectionState, dataChannel: opened, local: lc ? `${lc.candidateType}/${lc.protocol}` : null};
  a.close(); b.close();
  return result;
}
"""


def adb() -> str:
    found = shutil.which("adb") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe")
    if not Path(found).exists():
        raise SystemExit("adb not found")
    return found


def shell(cmd: str) -> str:
    return subprocess.run([adb(), "shell", cmd], capture_output=True, text=True, timeout=30).stdout


def preflight() -> str:
    if "\tdevice" not in subprocess.run([adb(), "devices"], capture_output=True, text=True).stdout:
        raise SystemExit("BLOCKED: no authorised Android device over USB")
    if "mWakefulness=Awake" not in shell("dumpsys power | grep mWakefulness="):
        raise SystemExit("BLOCKED: phone screen is off; wake and unlock it (the lock screen is never bypassed)")
    if "isKeyguardShowing=true" in shell("dumpsys window | grep isKeyguardShowing"):
        raise SystemExit("BLOCKED: phone is locked; unlock it (the lock screen is never bypassed)")
    if PACKAGE not in shell("dumpsys activity activities | grep topResumedActivity"):
        raise SystemExit("BLOCKED: bring 'SPPS RC Validation' to the foreground")
    pid = shell(f"pidof {PACKAGE}").strip()
    if not pid:
        raise SystemExit("BLOCKED: RC app process not running")
    return pid


def turn_server() -> dict:
    from server.ice_config import turn_credential
    from tools import turn_bench
    if turn_bench.problems(turn_bench.inspect()):
        raise SystemExit("BLOCKED: TURN bench not running (python -m tools.turn_bench up)")
    if "tcp:3478" not in subprocess.run([adb(), "reverse", "--list"], capture_output=True, text=True).stdout:
        subprocess.run([adb(), "reverse", "tcp:3478", "tcp:3478"], check=True, capture_output=True)
    username, credential, _ = turn_credential(turn_bench.secret(), "spps-probe", 600)
    return {"urls": [turn_bench.TURN_URL], "username": username, "credential": credential}


def main() -> int:
    import websockets.sync.client as wsc
    ap = argparse.ArgumentParser()
    ap.add_argument("--turn", action="store_true")
    args = ap.parse_args()
    pid = preflight()
    turn = turn_server() if args.turn else None
    subprocess.run([adb(), "forward", f"tcp:{FORWARD_PORT}", f"localabstract:webview_devtools_remote_{pid}"], check=True)
    try:
        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{FORWARD_PORT}/json", timeout=10))
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise SystemExit("BLOCKED: no WebView page target")
        with wsc.connect(pages[0]["webSocketDebuggerUrl"], max_size=2**22, open_timeout=10) as ws:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": f"({PROBE})({json.dumps(turn)})",
                                           "awaitPromise": True, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv(timeout=60))
                if msg.get("id") == 1:
                    break
    finally:
        subprocess.run([adb(), "forward", "--remove", f"tcp:{FORWARD_PORT}"], capture_output=True)
    result = msg.get("result", {})
    if "exceptionDetails" in result:
        print("probe exception:", str(result["exceptionDetails"].get("exception", {}).get("description", ""))[:300])
        return 1
    print(json.dumps(result["result"]["value"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
