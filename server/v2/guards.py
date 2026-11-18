"""Rate limits and the bot check shared by every public and auth endpoint.

Rate limits: fixed windows held per process. Render runs one worker per instance; with more than one instance put
a gateway limit in front as well (DEPLOYMENT.md). Keys never contain an e-mail in clear: e-mail keys are hashed.

Bot check: a signed form token. GET /api/public/form-token returns ``<issued_ms>.<nonce>.<hmac>``; a public write
must present it, at least SPPS_BOT_MIN_FILL_SECONDS (default 3) after issue and within two hours, plus an empty
honeypot field. The secret comes from SPPS_FORM_TOKEN_SECRET (required in hosted mode); locally a per-process
random secret is used. No third-party captcha is contacted, so the check also works inside the Android WebView.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_LOCK = threading.Lock()
_WINDOWS: dict[str, deque] = defaultdict(deque)
_LOCAL_SECRET = secrets.token_bytes(32)

# bucket -> (max requests, window seconds). One table so DEPLOYMENT.md and tests can list every limit.
LIMITS = {
    "form-token": (60, 600),
    "pending-application": (5, 3600),
    "pending-application-email": (3, 3600),
    "application-promote": (30, 600),
    "application-photo": (10, 600),
    "auth-exchange": (20, 600),
    "auth-login": (12, 600),
    "directory-search": (120, 600),
    "report-link": (60, 600),
    "portal-auth": (20, 600),
    "invite-redeem": (20, 600),
    "export": (5, 3600),
    # Global per-IP ceilings applied by the request middleware to every public and auth endpoint (phase 2).
    "public-read": (600, 600),
    "public-write": (60, 600),
    "auth-any": (120, 600),
}
GLOBAL_PREFIXES = (("/api/public/", "public"), ("/api/auth/", "auth"), ("/api/portal/auth/", "auth"),
                   ("/api/v2/public/", "public"), ("/api/v2/portal/auth/", "auth"), ("/r/", "public"),
                   ("/api/v2/report-links/", "public"), ("/api/v2/invites/", "public"),
                   ("/s/", "public"), ("/api/v2/shared-reports/", "public"))


def reset() -> None:
    """Tests only: forget every window."""
    with _LOCK:
        _WINDOWS.clear()


def client_ip(request: Request) -> str:
    from ..hosted_profile import client_ip as hosted_client_ip
    return hosted_client_ip(request)


def email_key(email: str) -> str:
    return hashlib.sha256(("spps-rl:" + email.strip().lower()).encode()).hexdigest()[:32]


def hit(bucket: str, key: str) -> None:
    """Count one request; raise 429 once the bucket's limit is reached."""
    limit, window = LIMITS[bucket]
    now = time.monotonic()
    with _LOCK:
        entries = _WINDOWS[bucket + ":" + key]
        while entries and entries[0] < now - window:
            entries.popleft()
        if len(entries) >= limit:
            raise HTTPException(429, "Too many requests; try again later")
        entries.append(now)


def limit_ip(request: Request, bucket: str) -> None:
    hit(bucket, client_ip(request))


def _secret() -> bytes:
    configured = os.getenv("SPPS_FORM_TOKEN_SECRET", "")
    if configured:
        return configured.encode()
    if os.getenv("MODEL_C_ENV", "local").lower() != "local":
        raise HTTPException(503, "Form protection is not configured")
    return _LOCAL_SECRET


def issue_form_token() -> str:
    issued = str(int(time.time() * 1000))
    nonce = secrets.token_urlsafe(12)
    sig = hmac.new(_secret(), f"{issued}.{nonce}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"{issued}.{nonce}.{sig}"


def min_fill_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("SPPS_BOT_MIN_FILL_SECONDS", "3")))
    except ValueError:
        return 3.0


def check_bot(form_token: str | None, honeypot: str | None) -> None:
    """Raise 400 for a missing/forged/too-fast/stale token or a filled honeypot. Same message for all cases."""
    refused = HTTPException(400, "Please reload the page and try again")
    if honeypot:
        raise refused
    parts = (form_token or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        raise refused
    expected = hmac.new(_secret(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(expected, parts[2]):
        raise refused
    age = time.time() - int(parts[0]) / 1000
    if age < min_fill_seconds() or age > 7200:
        raise refused


def global_limit(request: Request) -> None:
    """Per-IP ceiling for every public and auth endpoint, on top of the per-endpoint limits."""
    path = request.url.path
    for prefix, kind in GLOBAL_PREFIXES:
        if path.startswith(prefix):
            if kind == "auth":
                bucket = "auth-any"
            else:
                bucket = "public-read" if request.method in ("GET", "HEAD") else "public-write"
            hit(bucket, client_ip(request))
            return
