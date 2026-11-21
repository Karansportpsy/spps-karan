"""Phase 4 — notification devices, preferences and the first-sign-in prompt (SPPS_PUSH_ENABLED, needs the portal).

One set of handlers serves two kinds of person:
  * athletes and guardians: /api/portal/push/...  (the 13A portal session, whose cookie is scoped to /api/portal)
  * practitioners, admins and the owner: /api/v2/push/...  (workspace sign-in; identity functions ``practitioner_*``)

A person can only ever read or change their own devices and preferences: the identity functions resolve the owner
from the session token or the signed-in practitioner id, never from the request body. A stored subscription is never
returned to anyone; the list shows only the platform and dates. Messages are content-free (server/push_delivery.py).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import push_delivery
from ..portal_auth import sha256_hex
from . import guards
from .context import Ctx

CATEGORIES = ("appointments", "programme_tasks", "account_security")
SW_PATH = Path(__file__).resolve().parents[2] / "web" / "sw.js"

guards.LIMITS.setdefault("push-write", (60, 600))
guards.LIMITS.setdefault("push-read", (240, 600))


class SubscribeIn(BaseModel):
    platform: str = Field(pattern="^(web|android)$")
    subscription: dict


class UnsubscribeIn(BaseModel):
    device_id: int | None = Field(default=None, gt=0)
    endpoint: str | None = Field(default=None, max_length=4000)


class PreferenceIn(BaseModel):
    category: str
    enabled: bool


class PromptIn(BaseModel):
    answer: str = Field(pattern="^(ON|NOT_NOW)$")


def register(app: FastAPI, ctx: Ctx) -> None:
    get_current = ctx.get_current

    def portal_session(request: Request):
        current = getattr(request.app.state, "portal_current_session", None)
        if current is None:
            raise HTTPException(404, "Not found")
        return current(request)

    def call(name, **params):
        # identity_call passes the keyword arguments positionally, in order: the caller's identity always comes first.
        fn = getattr(app.state, "identity_call", None)
        if fn is None:
            raise HTTPException(404, "Not found")
        return fn(name, **params)

    def public_key() -> str:
        import os
        return os.environ.get("SPPS_VAPID_PUBLIC_KEY", "")

    # ---------------------------------------------------------------- shared behaviour
    def make(prefix: str, actor_dep, who_params, fn_prefix: str, fn_pref: str):
        """who_params(actor) -> the identity-function keyword that names the caller."""

        def state(request: Request, actor=Depends(actor_dep)):
            guards.limit_ip(request, "push-read")
            who = who_params(actor)
            prompt = call(f"{fn_prefix}_notification_prompt", **who)[0]
            devices = call(f"{fn_prefix}_push_devices", **who)
            prefs = call(f"{fn_pref}_notification_prefs", **who)
            return {"enabled": True, "public_key": public_key(),
                    "prompt": {"should_prompt": bool(prompt.should_prompt), "state": prompt.state,
                               "has_device": bool(prompt.has_device)},
                    "devices": [{"device_id": d.device_id, "platform": d.platform,
                                 "created_at": d.created_at.isoformat(), "last_seen_at": d.last_seen_at.isoformat()}
                                for d in devices],
                    "preferences": [{"category": p.category, "enabled": bool(p.enabled),
                                     "locked": p.category == "account_security"} for p in prefs]}

        def subscribe(body: SubscribeIn, request: Request, actor=Depends(actor_dep)):
            guards.limit_ip(request, "push-write")
            try:
                key, normalised = push_delivery.validate_subscription(body.platform, body.subscription)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            if body.platform == "web" and not push_delivery.vapid_ready():
                raise HTTPException(503, "Push notifications are not configured")
            who = who_params(actor)
            digest = hashlib.sha256(key.encode()).hexdigest()
            device_id = call(f"{fn_prefix}_push_register", **who, p_platform=body.platform, p_endpoint_hash=digest,
                             p_subscription=normalised)[0][0]
            call(f"{fn_prefix}_notification_answer", **who, p_answer="ON")
            return {"device_id": device_id}

        def unsubscribe(body: UnsubscribeIn, request: Request, actor=Depends(actor_dep)):
            guards.limit_ip(request, "push-write")
            if body.device_id is None and not body.endpoint:
                raise HTTPException(422, "Say which device")
            who = who_params(actor)
            digest = hashlib.sha256(body.endpoint.encode()).hexdigest() if body.endpoint else None
            n = call(f"{fn_prefix}_push_revoke", **who, p_device_id=body.device_id, p_endpoint_hash=digest)[0][0]
            return {"revoked": n}

        def preference(body: PreferenceIn, request: Request, actor=Depends(actor_dep)):
            guards.limit_ip(request, "push-write")
            if body.category not in CATEGORIES:
                raise HTTPException(422, "Unknown category")
            if body.category == "account_security" and not body.enabled:
                raise HTTPException(422, "Account and security messages cannot be switched off")
            call(f"{fn_pref}_notification_set", **who_params(actor), p_category=body.category, p_enabled=body.enabled)
            return {"category": body.category, "enabled": body.enabled}

        def prompt(body: PromptIn, request: Request, actor=Depends(actor_dep)):
            guards.limit_ip(request, "push-write")
            call(f"{fn_prefix}_notification_answer", **who_params(actor), p_answer=body.answer)
            return {"state": body.answer}

        app.get(f"{prefix}/state")(state)
        app.post(f"{prefix}/subscribe", status_code=201)(subscribe)
        app.post(f"{prefix}/unsubscribe")(unsubscribe)
        app.post(f"{prefix}/preferences")(preference)
        app.post(f"{prefix}/prompt")(prompt)

    make("/api/portal/push", portal_session, lambda s: {"p_token_hash": sha256_hex(s["token"])},
         "portal", "portal")
    make("/api/v2/push", get_current, lambda u: {"p_practitioner_id": u.id}, "practitioner", "practitioner")

    # The public key is not a secret; the page needs it before sign-in is complete on some surfaces.
    @app.get("/api/v2/push/config")
    def config(request: Request):
        guards.limit_ip(request, "push-read")
        return {"enabled": True, "public_key": public_key()}

    # The service worker must be served from the site root to control every page, and must never be cached for long.
    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        try:
            body = SW_PATH.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(404, "Not found") from None
        return Response(body, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
