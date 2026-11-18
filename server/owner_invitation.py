"""One-time verified owner invitation, never automatic email confirmation.

The legacy service-role key may be supplied to the isolated release runner
only; it MUST NOT be passed to browsers or the long-running FastAPI worker.
Invited owners must open the mailed invitation, verify their email, and enroll
in TOTP before they can claim administrator privileges.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx


class OwnerInviteRefused(RuntimeError):
    pass


def invite_owner(env: dict[str,str] | None = None) -> str:
    env = dict(os.environ) if env is None else env
    if env.get("SPPS_OWNER_INVITE_ON_STARTUP","false").lower() != "true":
        return "disabled"
    email = env.get("SPPS_OWNER_EMAIL","").strip().lower()
    if email != "karansportpsy@gmail.com":
        raise OwnerInviteRefused("Configured owner email does not match protected owner identity")
    project = env.get("SPPS_SUPABASE_URL","").rstrip("/")
    parsed = urlparse(project)
    ref = env.get("SPPS_SUPABASE_PROJECT_REF","")
    if (parsed.scheme != "https" or len(ref) != 20 or parsed.hostname != ref + ".supabase.co"
        or parsed.path not in ("","/")):
        raise OwnerInviteRefused("Supabase project URL does not match SPPS_SUPABASE_PROJECT_REF")
    key=env.get("SPPS_SUPABASE_SERVICE_ROLE_KEY","")
    if not key.startswith("eyJ") or len(key)<100:
        raise OwnerInviteRefused("A server-only Supabase service-role JWT is required")
    redirect = env.get("SPPS_PUBLIC_BASE_URL","").rstrip("/") + "/studio"
    if not redirect.startswith("https://"):
        raise OwnerInviteRefused("Owner invitation redirect must be the HTTPS public base URL")
    try:
        response=httpx.post(project + "/auth/v1/invite",
            params={"redirect_to":redirect},
            headers={"apikey":key,"Authorization":"Bearer "+key},
            json={"email":email},
            timeout=12,
            follow_redirects=False)
    except httpx.HTTPError as exc:
        raise OwnerInviteRefused("Supabase owner invitation request failed") from None
    if response.status_code in (200,201):
        return "invited"
    if response.status_code in (409,422):
        return "existing-user-review-required"
    raise OwnerInviteRefused("Supabase refused owner invitation (HTTP "+
                             str(response.status_code)+")")
