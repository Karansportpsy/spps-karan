"""Read-only acceptance evidence from the video room open in the physical RC app (SPPS-VIDEO-001).

    python -m tools.webview_room_stats            # one reading
    python -m tools.webview_room_stats --watch 60 # one reading every 5 s for 60 s

Reads ONLY the fixed diagnostic labels rendered by web/room.js (`#phase-*`): ICE, peer state,
selected path type, candidate type counts, inbound RTP and decoded frames. These labels never
contain addresses, tokens or SDP (web/room.js redaction rule). Nothing on the page is changed.
Same preflight as tools.webview_ice_probe: unlocked phone, RC app in the foreground.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

from tools.webview_ice_probe import FORWARD_PORT, adb, preflight

READ = r"""
(() => {
  if (!/^\/(video|room)\/\d+$/.test(location.pathname)) return {page: 'not a video room'};
  const out = {page: 'video room'};
  for (const k of ['signaling','participant','transport','gathering','candidates','remoteCandidates','ice',
                   'selectedPair','recovery','peer','remoteTrack','inboundVideo','videoFrames','inboundAudio','playback']) {
    const el = document.getElementById('phase-' + k); out[k] = el ? el.textContent : null;
  }
  const v = document.getElementById('remote-video');
  out.remoteVideoSize = v && v.videoWidth ? `${v.videoWidth}x${v.videoHeight}` : 'none';
  return out;
})()
"""


def read_once(ws) -> dict:
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": READ, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv(timeout=20))
        if msg.get("id") == 1:
            return msg["result"]["result"].get("value", {})


def main() -> int:
    import websockets.sync.client as wsc
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    pid = preflight()
    subprocess.run([adb(), "forward", f"tcp:{FORWARD_PORT}", f"localabstract:webview_devtools_remote_{pid}"], check=True)
    try:
        pages = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{FORWARD_PORT}/json", timeout=10))
                 if t.get("type") == "page"]
        with wsc.connect(pages[0]["webSocketDebuggerUrl"], open_timeout=10) as ws:
            end = time.monotonic() + args.watch
            while True:
                print(json.dumps(read_once(ws)), flush=True)
                if time.monotonic() >= end:
                    break
                time.sleep(5)
    finally:
        subprocess.run([adb(), "forward", "--remove", f"tcp:{FORWARD_PORT}"], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
