"""Supabase Auth identity verification and practitioner notification bridge.

The Supabase *publishable* key is safe to give to browsers. The backend never
trusts an OAuth redirect, email, MFA claim, or actor ID supplied without
validating the *same access token* through the Supabase Auth user endpoint.
All authorization remains server-owned by SPPS practitioner approval status.
"""
from __future__ import annotations

import base64
import json
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException


def enabled() -> bool:
    return os.getenv("SPPS_SUPABASE_AUTH_ENABLED", "false").lower() == "true"


def public_config() -> dict:
    url = os.getenv("SPPS_SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SPPS_SUPABASE_PUBLISHABLE_KEY", "")
    ready = enabled() and url.startswith("https://") and bool(key)
    return {"enabled": ready, "url": url if ready else "",
            "publishable_key": key if ready else "", "email_verification_required": True}


def verify_access_token(token: str, *, password_required: bool = False) -> dict:
    """Authenticate against GoTrue before using any decoded JWT claims."""
    cfg = public_config()
    if not cfg["enabled"]:
        raise HTTPException(503, "Supabase Auth is not configured on this server")
    if not token or len(token) > 8192 or token.count(".") != 2:
        raise HTTPException(401, "A valid Supabase session is required")
    try:
        response = httpx.get(
            cfg["url"] + "/auth/v1/user",
            headers={"apikey": cfg["publishable_key"],
                     "Authorization": "Bearer " + token},
            timeout=8.0,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise HTTPException(401, "Supabase authentication failed")
        user = response.json()
        uid = str(UUID(user["id"]))
        email = str(user["email"]).strip().lower()
        if not email or not user.get("email_confirmed_at") or user.get("is_anonymous"):
            raise HTTPException(403, "Verify your email before requesting practitioner approval")
        payload = json.loads(base64.urlsafe_b64decode(
            token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)))
        if payload.get("sub") != uid or payload.get("iss") != cfg["url"] + "/auth/v1":
            raise HTTPException(401, "Supabase identity does not match session")
        if password_required:
            methods = payload.get("amr")
            if (not isinstance(methods, list) or not any(
                isinstance(item, dict) and item.get("method") == "password"
                for item in methods
            )):
                raise HTTPException(403, "Sign in with email and password")
        metadata = user.get("app_metadata") or {}
        providers = metadata.get("providers") or []
        if not isinstance(providers, list):
            providers = []
        if metadata.get("provider") != "email" and "email" not in providers:
            raise HTTPException(403, "Use an email/password account, not social sign-in")
        return {"uid": uid, "email": email, "email_confirmed_at": user["email_confirmed_at"],
                "aal": payload.get("aal"), "provider": metadata.get("provider"),
                # v2: user_metadata carries the pending-application claim; untrusted text, never authority.
                "user_metadata": user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}}
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Supabase authentication token") from None
    except httpx.HTTPError:
        raise HTTPException(503, "Supabase authentication service unavailable") from None


def send_review_email(destination: str, name: str, decision: str, note: str = "") -> bool:
    """Send after database commit; missing mail credentials means NOT delivered."""
    host = os.getenv("SPPS_SMTP_HOST", "")
    username = os.getenv("SPPS_SMTP_USERNAME", "")
    password = os.getenv("SPPS_SMTP_PASSWORD", "")
    sender = os.getenv("SPPS_SMTP_FROM", "")
    if not all((host, username, password, sender)):
        return False
    if decision == "approved":
        base = os.getenv("SPPS_PUBLIC_BASE_URL", "").strip().rstrip("/") or "https://app.winmindperform.com"
        subject = "Your SPPS practitioner profile has been approved"
        body = (f"Hello {name},\n\nYour SPPS practitioner profile has been approved. "
                f"You can now sign in to the platform using this link:\n\n{base}/studio\n\n"
                "Use the email address and password you registered with. "
                "You may now use the free practitioner workspace. "
                "Your approved profile is eligible for the public directory. Your private contact details are not displayed.\n")
    elif decision == "request_more_info":
        subject = "SPPS practitioner verification: more information needed"
        body = f"Hello {name},\n\nThe SPPS reviewer requests additional information:\n{note}\n"
    else:
        subject = "SPPS practitioner verification update"
        body = f"Hello {name},\n\nYour practitioner application was not approved.\n{note}\n"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, destination
    msg.set_content(body + "\nSPPS by WinMindPerform\n")
    try:
        port = int(os.getenv("SPPS_SMTP_PORT", "587"))
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as client:
                client.login(username, password)
                client.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as client:
                client.starttls()
                client.login(username, password)
                client.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, ValueError):
        return False
