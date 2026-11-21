"""Phase 4 (notifications): subscription safety, content-free payloads, send outcomes, flags, preflight, allowlist,
the sender-address rules and the browser files. The database behaviour is proved in tests_v2_pg/test_notifications_phase4.py."""
import json
from pathlib import Path
from unittest import mock

import pytest

from .test_phase9_release import MAIL
from .test_v2_platform_contracts import _env

ROOT = Path(__file__).resolve().parents[1]
P256, AUTH = "Q" * 87, "a" * 22
GOOD = "https://fcm.googleapis.com/fcm/send/abc123"
VAPID = {"SPPS_VAPID_PUBLIC_KEY": "B" * 87, "SPPS_VAPID_PRIVATE_KEY": "c" * 43,
         "SPPS_VAPID_SUBJECT": "mailto:ops@example.org"}


def web(endpoint=GOOD, p256=P256, auth=AUTH):
    return {"endpoint": endpoint, "keys": {"p256dh": p256, "auth": auth}}


# ------------------------------------------------------------------ what a browser may register
@pytest.mark.parametrize("endpoint", [GOOD, "https://updates.push.services.mozilla.com/wpush/v2/xyz",
                                      "https://web.push.apple.com/QWERTY", "https://wns2-par02p.notify.windows.com/w/?token=x"])
def test_known_push_services_are_accepted(endpoint):
    from server.push_delivery import validate_subscription
    key, normalised = validate_subscription("web", web(endpoint))
    assert key == endpoint and json.loads(normalised)["keys"]["auth"] == AUTH


@pytest.mark.parametrize("endpoint", ["http://fcm.googleapis.com/x", "https://evil.example/x", "https://fcm.googleapis.com.evil.example/x",
                                      "https://u:p@fcm.googleapis.com/x", "https://fcm.googleapis.com:444/x", "https://127.0.0.1/x",
                                      "https://169.254.169.254/latest", "ftp://fcm.googleapis.com/x", "", None, 5])
def test_other_destinations_are_refused(endpoint):
    from server.push_delivery import validate_subscription
    with pytest.raises(ValueError):
        validate_subscription("web", web(endpoint))


@pytest.mark.parametrize("p256,auth", [("short", AUTH), (P256, "x"), ("!" * 87, AUTH), (P256, "a b" * 8), (None, AUTH)])
def test_bad_keys_are_refused(p256, auth):
    from server.push_delivery import validate_subscription
    with pytest.raises(ValueError):
        validate_subscription("web", web(p256=p256, auth=auth))


def test_android_token_rules():
    from server.push_delivery import validate_subscription
    assert validate_subscription("android", {"token": "t" * 60})[0] == "t" * 60
    for bad in ({"token": "short"}, {"token": "x y" * 10}, {"token": 5}, {}):
        with pytest.raises(ValueError):
            validate_subscription("android", bad)
    with pytest.raises(ValueError):
        validate_subscription("ios", {"token": "t" * 60})


def test_payloads_are_content_free_for_every_kind():
    from server.notifications import SUBJECTS
    from server.push_delivery import TITLES, payload_for
    assert set(TITLES) <= set(SUBJECTS) | {"booking.reminder"}
    for kind in list(TITLES) + ["something.else"]:
        p = payload_for(kind, "https://x.example/portal")
        assert set(p) == {"title", "body", "url", "kind"}
        assert "@" not in json.dumps(p) and len(p["title"]) < 60


# ------------------------------------------------------------------ what a push service answers
def _sender():
    from server.push_delivery import PushSender
    return PushSender({**VAPID})


@pytest.mark.parametrize("status,outcome", [(404, "dead"), (410, "dead"), (500, "retry"), (429, "retry"), (None, "retry")])
def test_web_push_outcomes(status, outcome):
    from pywebpush import WebPushException
    exc = WebPushException("x", response=mock.Mock(status_code=status) if status else None)
    with mock.patch("pywebpush.webpush", side_effect=exc):
        assert _sender().send("web", json.dumps(web()), {"title": "t", "body": "b", "url": "/", "kind": "k"}) == outcome


def test_web_push_ok_and_foreign_endpoint_never_sent():
    with mock.patch("pywebpush.webpush") as sent:
        assert _sender().send("web", json.dumps(web()), {"title": "t", "body": "b", "url": "/", "kind": "k"}) == "ok"
        sent.assert_called_once()
        assert sent.call_args.kwargs["vapid_claims"] == {"sub": VAPID["SPPS_VAPID_SUBJECT"]}
        sent.reset_mock()
        assert _sender().send("web", json.dumps(web("https://evil.example/x")), {}) == "dead"
        sent.assert_not_called()


def test_corrupt_stored_subscription_is_dropped_and_android_without_credentials_retries():
    assert _sender().send("web", "{not json", {}) == "dead"
    assert _sender().send("android", json.dumps({"token": "t" * 40}), {}) == "retry"


# ------------------------------------------------------------------ flags, preflight, allowlist
def test_flags_default_off_and_need_the_portal(monkeypatch):
    from server.v2 import flags
    for k in ("SPPS_PUSH_ENABLED", "SPPS_NOTIFY_WORKER_ENABLED", "SPPS_PORTAL_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    assert flags.FLAGS["SPPS_PUSH_ENABLED"][0] == "false" and flags.FLAGS["SPPS_NOTIFY_WORKER_ENABLED"][0] == "false"
    monkeypatch.setenv("SPPS_PUSH_ENABLED", "true")
    monkeypatch.setenv("SPPS_NOTIFY_WORKER_ENABLED", "true")
    assert not flags.push_enabled() and not flags.notify_worker_enabled()
    monkeypatch.setenv("SPPS_PORTAL_ENABLED", "true")
    assert flags.push_enabled() and flags.notify_worker_enabled()


def test_preflight_rules_for_push_and_the_worker():
    from server.v2 import preflight
    assert preflight.FLAG_MIGRATIONS["SPPS_PUSH_ENABLED"] == ["0046_v2_notifications_push.sql"]
    on = {"SPPS_PORTAL_ENABLED": "true", "SPPS_PUSH_ENABLED": "true"}
    with pytest.raises(preflight.PreflightError, match="SPPS_VAPID_PUBLIC_KEY"):
        preflight.validate_env(_env(**on))
    with pytest.raises(preflight.PreflightError, match="SPPS_VAPID_SUBJECT"):
        preflight.validate_env(_env(**on, **{**VAPID, "SPPS_VAPID_SUBJECT": "ops@example.org"}))
    preflight.validate_env(_env(**on, **VAPID))
    with pytest.raises(preflight.PreflightError, match="SPPS_PORTAL_ENABLED"):
        preflight.validate_env(_env(SPPS_PUSH_ENABLED="true", **VAPID))
    with pytest.raises(preflight.PreflightError, match="SPPS_SMTP_HOST"):
        preflight.validate_env(_env(SPPS_PORTAL_ENABLED="true", SPPS_NOTIFY_WORKER_ENABLED="true"))
    preflight.validate_env(_env(SPPS_PORTAL_ENABLED="true", SPPS_NOTIFY_WORKER_ENABLED="true", **MAIL))


@pytest.mark.parametrize("sender,ok", [("no-reply@example.org", True), ("SPPS <no-reply@example.org>", True),
                                       ("Name <bad>", False), ("a b@example.org", False), ("<>", False)])
def test_sender_address_accepts_a_display_name(sender, ok):
    from server.v2 import preflight
    on = {"SPPS_PORTAL_ENABLED": "true", "SPPS_ATHLETE_LOOP_ENABLED": "true", **{**MAIL, "SPPS_SMTP_FROM": sender}}
    if ok:
        preflight.validate_env(_env(**on))
    else:
        with pytest.raises(preflight.PreflightError, match="SPPS_SMTP_FROM"):
            preflight.validate_env(_env(**on))


def test_hosted_portal_accepts_sender_or_from():
    from server.hosted_profile import HostedProfileError, validate_portal
    base = {"SPPS_SMTP_HOST": "smtp.example.org", "SPPS_PUBLIC_BASE_URL": "https://app.example.org",
            "MODEL_C_ALLOWED_HOSTS": "app.example.org"}
    for extra in ({}, {"SPPS_SMTP_SENDER": ""}):
        with pytest.raises(HostedProfileError, match="SPPS_SMTP_SENDER"):
            validate_portal({**base, **extra}, "spps_identity_dev")
    # either variable satisfies the sender requirement; any later refusal must be about something else
    for extra in ({"SPPS_SMTP_SENDER": "a@example.org"}, {"SPPS_SMTP_FROM": "SPPS <a@example.org>"}):
        try:
            validate_portal({**base, **extra}, "spps_identity_dev")
        except HostedProfileError as exc:
            assert "SPPS_SMTP_SENDER" not in str(exc)


def test_smtp_provider_uses_from_when_sender_is_not_set(monkeypatch):
    from server.notifications import provider_from_env
    for k in ("SPPS_SMTP_SENDER", "SPPS_NOTIFY_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SPPS_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SPPS_SMTP_FROM", "SPPS <no-reply@example.org>")
    assert provider_from_env("hosted").sender == "SPPS <no-reply@example.org>"
    monkeypatch.setenv("SPPS_SMTP_SENDER", "other@example.org")
    assert provider_from_env("hosted").sender == "other@example.org"


def test_allowlist_covers_exactly_the_push_routes(monkeypatch):
    from server.v2.allowlist import v2_allowed
    monkeypatch.setenv("SPPS_PORTAL_ENABLED", "true")
    monkeypatch.setenv("SPPS_PUSH_ENABLED", "false")
    assert v2_allowed("GET", "/api/v2/push/state") is False and v2_allowed("POST", "/api/portal/push/subscribe") is False
    monkeypatch.setenv("SPPS_PUSH_ENABLED", "true")
    for method, path in [("GET", "/api/v2/push/state"), ("GET", "/api/portal/push/state"), ("GET", "/api/v2/push/config"),
                         ("POST", "/api/v2/push/subscribe"), ("POST", "/api/portal/push/unsubscribe"),
                         ("POST", "/api/portal/push/preferences"), ("POST", "/api/v2/push/prompt")]:
        assert v2_allowed(method, path) is True, (method, path)
    for method, path in [("DELETE", "/api/v2/push/state"), ("GET", "/api/v2/push/subscribe"), ("POST", "/api/v2/push/other"),
                         ("GET", "/api/v2/push/devices")]:
        assert v2_allowed(method, path) is not True, (method, path)


def test_rate_limits_exist_and_every_handler_uses_them():
    from server.v2 import guards, push  # noqa: F401 — importing registers the limits
    assert guards.LIMITS["push-write"][0] <= 60
    src = (ROOT / "server" / "v2" / "push.py").read_text(encoding="utf-8")
    for fn in ("def state", "def subscribe", "def unsubscribe", "def preference", "def prompt"):
        assert "limit_ip(" in src.split(fn)[1].split("\n        def ")[0], fn


# ------------------------------------------------------------------ browser files and housekeeping
def test_service_worker_is_content_free_and_stays_on_this_site():
    sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
    assert "caches." not in sw and "fetch" not in sw.replace("self.clients", "")
    assert "origin" in sw and "openWindow" in sw
    assert (ROOT / "www" / "sw.js").read_bytes() == (ROOT / "web" / "sw.js").read_bytes()


def test_push_script_uses_no_browser_storage_and_is_staged():
    js = (ROOT / "web" / "push.js").read_text(encoding="utf-8")
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert banned not in js, banned
    assert (ROOT / "www" / "assets" / "push.js").read_bytes() == (ROOT / "web" / "push.js").read_bytes()
    for page in ("portal.html", "index.html"):
        html = (ROOT / "web" / page).read_text(encoding="utf-8")
        assert html.index("/assets/push.js") < html.index("/assets/portal.js" if page == "portal.html" else "/assets/app.js")
    assert "push.js" in (ROOT / "scripts" / "stage-web.mjs").read_text(encoding="utf-8")


def test_dependencies_are_pinned_and_keys_are_not_committed():
    reqs = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8")
    assert "pywebpush==" in reqs and "google-auth==" in reqs
    for f in (".env.example", ".env.production.example", "render.yaml"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "SPPS_VAPID_PRIVATE_KEY" in text
        for line in text.splitlines():
            if "SPPS_VAPID_PRIVATE_KEY" in line and "=" in line:
                assert line.split("=", 1)[1].strip().startswith("<") or not line.split("=", 1)[1].strip()
