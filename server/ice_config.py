"""ICE server configuration for consented video rooms (SPPS-VIDEO-001).

Without configuration nothing external is used (host candidates only), which only works when both
peers share a routable network. For anything else configure:

* ``MODEL_C_ICE_URLS``: comma-separated ``stun:``/``turn:``/``turns:`` URLs, e.g.
  ``stun:stun.example.org:3478,turn:turn.example.org:3478?transport=udp,turns:turn.example.org:443?transport=tcp``.
  The legacy single-URL ``MODEL_C_STUN_URL`` / ``MODEL_C_TURN_URL`` are still honoured.
* ``MODEL_C_TURN_SECRET`` (preferred): shared secret of a coturn ``use-auth-secret`` server. Each room
  request gets a *short-lived* credential (TURN REST API scheme: username ``<unix-expiry>:<label>``,
  credential ``base64(HMAC-SHA1(secret, username))``), so no reusable password reaches a browser.
  ``MODEL_C_TURN_TTL_SECONDS`` (default 3600, range 300-7200).
* ``MODEL_C_TURN_USERNAME`` / ``MODEL_C_TURN_CREDENTIAL``: legacy static credential; refused in
  staging/production, where only ``MODEL_C_TURN_SECRET`` is accepted.
* ``MODEL_C_ICE_TRANSPORT_POLICY``: ``all`` (default) or ``relay`` (forces TURN; used by the test bench).

The secret never leaves the server; the response contains only derived, expiring credentials.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

SCHEMES = ("stun:", "turn:", "turns:")
MIN_SECRET = 32
DEFAULT_TTL, MIN_TTL, MAX_TTL = 3600, 300, 7200


class IceConfigError(RuntimeError):
    """Configuration present but unusable; the route answers 503 without detail."""


def _urls(env) -> list[str]:
    raw = [u.strip() for u in env.get("MODEL_C_ICE_URLS", "").split(",") if u.strip()]
    raw += [env[k].strip() for k in ("MODEL_C_STUN_URL", "MODEL_C_TURN_URL") if env.get(k, "").strip()]
    seen, out = set(), []
    for url in raw:
        if not url.lower().startswith(SCHEMES) or any(c.isspace() for c in url):
            raise IceConfigError("Unsupported ICE URL scheme")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def turn_credential(secret: str, label: str, ttl: int, now: float | None = None) -> tuple[str, str, int]:
    expires = int(now if now is not None else time.time()) + ttl
    username = f"{expires}:{label}"
    digest = hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode("ascii"), expires


def ice_config(label: str, mode: str = "local", env=None, now: float | None = None) -> dict:
    env = os.environ if env is None else env
    urls = _urls(env)
    policy = env.get("MODEL_C_ICE_TRANSPORT_POLICY", "all").strip().lower() or "all"
    if policy not in ("all", "relay"):
        raise IceConfigError("Invalid ICE transport policy")
    stun = [u for u in urls if u.lower().startswith("stun:")]
    turn = [u for u in urls if not u.lower().startswith("stun:")]
    servers: list[dict] = [{"urls": stun}] if stun else []
    result = {"iceTransportPolicy": policy}
    if turn:
        secret = env.get("MODEL_C_TURN_SECRET", "")
        if secret:
            if len(secret) < MIN_SECRET:
                raise IceConfigError("TURN secret too short")
            try:
                ttl = int(env.get("MODEL_C_TURN_TTL_SECONDS", DEFAULT_TTL))
            except ValueError:
                raise IceConfigError("Invalid TURN TTL") from None
            ttl = max(MIN_TTL, min(MAX_TTL, ttl))
            username, credential, expires = turn_credential(secret, label, ttl, now)
            result["credentialExpiresAt"] = expires
        elif mode in ("staging", "production"):
            raise IceConfigError("Hosted TURN requires MODEL_C_TURN_SECRET (short-lived credentials)")
        else:
            username, credential = env.get("MODEL_C_TURN_USERNAME", ""), env.get("MODEL_C_TURN_CREDENTIAL", "")
            if not username or not credential:
                raise IceConfigError("TURN credentials not configured")
        servers.append({"urls": turn, "username": username, "credential": credential})
    elif policy == "relay":
        raise IceConfigError("Relay-only policy needs a TURN URL")
    result["iceServers"] = servers
    return result
