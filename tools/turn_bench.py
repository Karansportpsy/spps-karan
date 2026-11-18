"""SPPS-VIDEO-001 TURN test bench (disposable coturn, TCP 127.0.0.1:3478 only). Fictional use only.

    python -m tools.turn_bench up        # generate secret, start spps-test-turn, verify, probe listener
    python -m tools.turn_bench status    # labels/bindings/mounts + STUN-over-TCP listener probe
    python -m tools.turn_bench selftest  # two relay-only Chromium peers through the bench (no SPPS backend)
    python -m tools.turn_bench down      # stop and remove ONLY spps-test-turn; delete the secret

Network design (see docs/DEVICE_VALIDATION_GUIDE.md "TURN test bench"):
Windows Chrome -> TCP 127.0.0.1:3478 -> Docker Desktop port proxy -> coturn (10.231.77.10)
Android WebView -> TCP 127.0.0.1:3478 on the phone -> adb reverse (USB) -> same listener
Both peers use iceTransportPolicy=relay, so media is relayed allocation-to-allocation inside the
container. Nothing listens on a LAN interface; no relay port is published; no firewall change.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.test-turn.yml"
PROJECT = "spps-test-turn"
CONTAINER = "spps-test-turn-turn-1"
PURPOSE = "turn-test-bench-only"
PORT = 3478
RELAY_IP = "10.231.77.10"
STATE = ROOT / "output" / "device-validation"
SECRET_FILE = STATE / "turn.env"
TURN_URL = f"turn:127.0.0.1:{PORT}?transport=tcp"


@dataclass(frozen=True)
class Facts:
    project: str | None
    purpose: str | None
    running: bool
    bindings: dict
    mounts: list
    image: str | None


def inspect(name: str = CONTAINER) -> Facts | None:
    try:
        data = json.loads(subprocess.check_output(["docker", "inspect", name], text=True,
                                                  stderr=subprocess.DEVNULL, timeout=20))[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
        return None
    labels = data.get("Config", {}).get("Labels", {}) or {}
    return Facts(project=labels.get("com.docker.compose.project"), purpose=labels.get("org.spps.purpose"),
                 running=data.get("State", {}).get("Running") is True,
                 bindings=(data.get("HostConfig", {}) or {}).get("PortBindings", {}) or {},
                 mounts=data.get("Mounts", []) or [], image=data.get("Config", {}).get("Image"))


def problems(facts: Facts | None) -> list[str]:
    if facts is None:
        return ["turn bench container not found"]
    out = []
    if facts.project != PROJECT or facts.purpose != PURPOSE:
        out.append("container is not the spps-test-turn bench")
    if not facts.running:
        out.append("container not running")
    if set(facts.bindings) != {f"{PORT}/tcp"}:
        out.append("published ports must be exactly 3478/tcp")
    else:
        b = facts.bindings[f"{PORT}/tcp"] or []
        if not (len(b) == 1 and b[0].get("HostIp") == "127.0.0.1" and b[0].get("HostPort") == str(PORT)):
            out.append("binding is not exactly 127.0.0.1:3478")
    if facts.mounts:
        out.append("bench must have no volumes or bind mounts")
    return out


def stun_probe(host: str = "127.0.0.1", port: int = PORT, timeout: float = 3.0) -> bool:
    """STUN Binding request over TCP: proves a STUN/TURN server (not just any socket) answers."""
    tid = secrets.token_bytes(12)
    request = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(request)
            head = s.recv(20)
    except OSError:
        return False
    return len(head) == 20 and struct.unpack("!H", head[:2])[0] == 0x0101 and head[8:20] == tid


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _compose(*args: str, secret: str = "unused") -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "-p", PROJECT, *args], check=True, cwd=ROOT,
                   env={**os.environ, "SPPS_TURN_SECRET": secret}, timeout=300)


def secret() -> str:
    from dotenv import dotenv_values
    value = dotenv_values(SECRET_FILE).get("MODEL_C_TURN_SECRET", "") if SECRET_FILE.exists() else ""
    if not value:
        raise SystemExit("TURN bench secret missing; run `python -m tools.turn_bench up`")
    return value


def backend_env(policy: str = "relay") -> dict[str, str]:
    """Settings merged into the API environment when the bench is in use (never committed).
    policy="all" keeps host candidates too, so a shared Wi-Fi still works if a client cannot reach the bench."""
    if policy not in ("all", "relay"):
        raise ValueError("policy must be all or relay")
    return {"MODEL_C_ICE_URLS": TURN_URL, "MODEL_C_TURN_SECRET": secret(),
            "MODEL_C_TURN_TTL_SECONDS": "3600", "MODEL_C_ICE_TRANSPORT_POLICY": policy}


def up() -> None:
    facts = inspect()
    if facts is not None and (facts.project != PROJECT or facts.purpose != PURPOSE):
        raise SystemExit("STOP: a container named like the TURN bench is not ours; refusing")
    if facts is None and _port_in_use(PORT):
        raise SystemExit(f"STOP: 127.0.0.1:{PORT} is already used by another program; nothing started")
    value = secrets.token_urlsafe(48)
    STATE.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.unlink(missing_ok=True)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"MODEL_C_TURN_SECRET={value}\n")
    _compose("up", "-d", "--force-recreate", secret=value)
    issues = problems(inspect())
    if issues:
        print(logs())
        down()
        raise SystemExit("STOP: TURN bench failed verification: " + "; ".join(issues))
    import time
    for _ in range(50):
        if stun_probe():
            print(f"PASS: TURN bench listening on 127.0.0.1:{PORT}/tcp (STUN binding answered); relay {RELAY_IP}")
            return
        time.sleep(0.2)
    raise SystemExit("FAIL: TURN bench started but did not answer a STUN binding request")


def logs(lines: int = 40) -> str:
    """Last container log lines with any secret-looking value redacted."""
    import re
    r = subprocess.run(["docker", "logs", "--tail", str(lines), CONTAINER], capture_output=True, text=True, timeout=30)
    text = (r.stdout or "") + (r.stderr or "")
    return re.sub(r"(secret|password|credential)\S*", r"=[REDACTED]", text, flags=re.I)


def down() -> None:
    facts = inspect()
    if facts is not None and (facts.project != PROJECT or facts.purpose != PURPOSE):
        raise SystemExit("STOP: a container named like the TURN bench is not ours; refusing")
    if facts is not None:
        _compose("down", "--remove-orphans")  # no -v: the bench has no volumes
    SECRET_FILE.unlink(missing_ok=True)
    print("TURN bench stopped; secret deleted.")


def status() -> int:
    issues = problems(inspect())
    print("turn bench:", "OK" if not issues else "; ".join(issues))
    print("listener:", "STUN binding answered" if stun_probe() else "no answer")
    return 0 if not issues else 1


SELFTEST_JS = r"""
async ({turn, username, credential}) => {
  const cfg = {iceServers: [{urls: [turn], username, credential}], iceTransportPolicy: 'relay'};
  const media = await navigator.mediaDevices.getUserMedia({audio: true, video: true});
  const a = new RTCPeerConnection(cfg), b = new RTCPeerConnection(cfg);
  const types = {a: {}, b: {}};
  a.onicecandidate = e => { if (e.candidate) { types.a[e.candidate.type] = (types.a[e.candidate.type] || 0) + 1; b.addIceCandidate(e.candidate); } };
  b.onicecandidate = e => { if (e.candidate) { types.b[e.candidate.type] = (types.b[e.candidate.type] || 0) + 1; a.addIceCandidate(e.candidate); } };
  const media2 = await navigator.mediaDevices.getUserMedia({audio: true, video: true});
  media.getTracks().forEach(t => a.addTrack(t, media));
  media2.getTracks().forEach(t => b.addTrack(t, media2));
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
  await new Promise(r => setTimeout(r, 8000));
  const pairOf = async pc => { const s = await pc.getStats(); let sel = null, bytes = 0;
    s.forEach(x => { if (x.type === 'transport' && x.selectedCandidatePairId) sel = s.get(x.selectedCandidatePairId);
                     if (x.type === 'inbound-rtp') bytes += x.bytesReceived || 0; });
    const l = sel && s.get(sel.localCandidateId);
    return {ice: pc.iceConnectionState, local: l ? `${l.candidateType}/${l.relayProtocol || '-'}` : null, inboundBytes: bytes}; };
  const out = {types, a: await pairOf(a), b: await pairOf(b)};
  a.close(); b.close(); [media, media2].forEach(m => m.getTracks().forEach(t => t.stop()));
  return out;
}
"""


def selftest() -> int:
    from playwright.sync_api import sync_playwright

    from server.ice_config import turn_credential
    if problems(inspect()):
        raise SystemExit("TURN bench not running; `python -m tools.turn_bench up` first")
    username, credential, _ = turn_credential(secret(), "spps-selftest", 600)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        page = browser.new_context(permissions=["camera", "microphone"]).new_page()
        page.route("https://turn-selftest.test/**", lambda r: r.fulfill(body="<p>selftest</p>", content_type="text/html"))
        page.goto("https://turn-selftest.test/")
        result = page.evaluate(SELFTEST_JS, {"turn": TURN_URL, "username": username, "credential": credential})
        browser.close()
    print(json.dumps(result, indent=2))  # candidate types and byte counts only
    ok = all(result[k]["ice"] in ("connected", "completed") and result[k]["local"] == "relay/tcp"
             and result[k]["inboundBytes"] > 0 and not set(result["types"][k]) - {"relay"} for k in ("a", "b"))
    print("PASS: relay-only media through the TURN bench" if ok else "FAIL: relay path not established")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    commands = {"up": lambda: up() or 0, "down": lambda: down() or 0, "status": status, "selftest": selftest}
    if len(argv) != 1 or argv[0] not in commands:
        print(__doc__)
        return 2
    return commands[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
