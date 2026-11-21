"""Phase 4 — push delivery for the platform outbox (Web Push for browsers, FCM for the Android app).

Payloads are content-free, exactly like e-mail (server/notifications.py): a short title, a generic line and the page to
open. No names, times, clinical text or tokens are ever placed in a push message.

Outcomes of one send are ``ok``, ``dead`` (the provider says the device is gone: never retry it) or ``retry``
(a temporary failure). Libraries are imported lazily so the API starts without them when push is off.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

# Push services a browser can hand us. The server POSTs to the subscription endpoint, so anything else is refused
# at registration (server-side request forgery guard) and again before every send.
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com",
)
_B64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")

TITLES = {
    "booking.requested": "New appointment request",
    "booking.approved_payment_due": "Appointment approved",
    "booking.confirmed": "Appointment confirmed",
    "booking.declined": "Appointment request update",
    "booking.expired": "Appointment request expired",
    "booking.reschedule_proposed": "New appointment time proposed",
    "booking.reschedule_declined": "Appointment time update",
    "booking.reminder": "Appointment reminder",
    "programme.assigned": "New programme",
    "programme.feedback": "New feedback",
    "summary.released": "New update from your practitioner",
}
GENERIC_TITLE = "You have an update"


def allowed_endpoint(endpoint: str) -> bool:
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.password or (u.port not in (None, 443)):
        return False
    return any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


def validate_subscription(platform: str, sub: dict) -> tuple[str, str]:
    """Returns (unique device key, normalised JSON). Raises ValueError with a safe message."""
    if platform == "web":
        endpoint = sub.get("endpoint")
        keys = sub.get("keys") or {}
        p256, auth = keys.get("p256dh"), keys.get("auth")
        if not isinstance(endpoint, str) or len(endpoint) > 1000 or not allowed_endpoint(endpoint):
            raise ValueError("This browser's push service is not supported")
        if not (isinstance(p256, str) and isinstance(auth, str) and _B64URL.match(p256) and _B64URL.match(auth)
                and 60 <= len(p256) <= 100 and 16 <= len(auth) <= 40):
            raise ValueError("Invalid push keys")
        return endpoint, json.dumps({"endpoint": endpoint, "keys": {"p256dh": p256, "auth": auth}},
                                    separators=(",", ":"))
    if platform == "android":
        token = sub.get("token")
        if not isinstance(token, str) or not (20 <= len(token) <= 4000) or not re.match(r"^[A-Za-z0-9_:.\-]+$", token):
            raise ValueError("Invalid device token")
        return token, json.dumps({"token": token}, separators=(",", ":"))
    raise ValueError("Unknown platform")


def payload_for(kind: str, url: str) -> dict:
    return {"title": TITLES.get(kind, GENERIC_TITLE),
            "body": "Open SPPS to see it. Details are not shown here for your privacy.",
            "url": url, "kind": kind}


class PushSender:
    """Sends one content-free payload to one registered device."""

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ
        self._fcm_token = None

    # -------------------------------------------------------------- Web Push
    def send_web(self, subscription: dict, payload: dict) -> str:
        if not allowed_endpoint(subscription.get("endpoint", "")):
            return "dead"
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:  # pragma: no cover - dependency is pinned in requirements-hosted.txt
            return "retry"
        try:
            webpush(subscription_info=subscription, data=json.dumps(payload), ttl=3600,
                    vapid_private_key=self.env["SPPS_VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": self.env["SPPS_VAPID_SUBJECT"]})
            return "ok"
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return "dead" if status in (404, 410) else "retry"
        except Exception:  # noqa: BLE001 — network or key trouble: try again later
            return "retry"

    # -------------------------------------------------------------- FCM HTTP v1 (Android)
    def _fcm_access_token(self):
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        info = json.loads(self.env["SPPS_FCM_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        creds.refresh(Request())
        return creds.token, info["project_id"]

    def send_android(self, subscription: dict, payload: dict) -> str:
        if not self.env.get("SPPS_FCM_SERVICE_ACCOUNT_JSON"):
            return "retry"
        try:
            import urllib.error
            import urllib.request
            token, project = self._fcm_access_token()
            body = json.dumps({"message": {"token": subscription["token"],
                                           "notification": {"title": payload["title"], "body": payload["body"]},
                                           "data": {"url": payload["url"], "kind": payload["kind"]},
                                           "android": {"priority": "HIGH"}}}).encode()
            req = urllib.request.Request(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send", data=body,
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15):  # noqa: S310 — fixed https host
                return "ok"
        except urllib.error.HTTPError as exc:
            return "dead" if exc.code in (404, 410) else "retry"
        except Exception:  # noqa: BLE001
            return "retry"

    def send(self, platform: str, subscription_json: str, payload: dict) -> str:
        try:
            sub = json.loads(subscription_json)
        except ValueError:
            return "dead"
        if platform == "web":
            return self.send_web(sub, payload)
        if platform == "android":
            return self.send_android(sub, payload)
        return "dead"


def vapid_ready(env=None) -> bool:
    env = env if env is not None else os.environ
    return all(env.get(k) for k in ("SPPS_VAPID_PUBLIC_KEY", "SPPS_VAPID_PRIVATE_KEY", "SPPS_VAPID_SUBJECT"))
