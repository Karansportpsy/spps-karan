"""Stage 13A.5b — athlete and guardian ("portal") authentication API.

Completely separate from practitioner auth (`/api/auth/*`, `modelc_session`):
  * own cookie `spps_portal_session` scoped to `/api/portal`, HttpOnly, SameSite=Strict
  * own PostgreSQL database + roles: `spps_identity_auth` may only EXECUTE the
    auth_* SECURITY DEFINER functions (migration 0005); `spps_identity_portal`
    reads RLS projections bound to the session token hash.
  * enumeration-safe responses, constant-work password checks, PostgreSQL-backed
    throttling, CSRF bound to the session, idle + absolute expiry, revocation.
Verification / reset messages go to a delivery adapter. Only the local file
outbox exists today; hosted mode refuses to enable the portal without a real
provider (Phase P1).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .security import make_password, verify_password

COOKIE = "spps_portal_session"
COOKIE_PATH = "/api/portal"
SESSION_TTL_SECONDS = 12 * 3600
VERIFY_TTL_SECONDS = 24 * 3600
RESET_TTL_SECONDS = 30 * 60
GENERIC_LOGIN_ERROR = "Invalid email or password"
ACCEPTED = {"status": "accepted",
            "message": "If the details are valid, we have sent a message to that address."}
COMMON_PASSWORDS = frozenset({
    "password1234", "password12345", "123456789012", "qwertyuiopas", "letmein12345",
    "welcome12345", "iloveyou1234", "admin1234567", "passwordpassword", "sportpsychology",
})
# Constant-work dummy hash so unknown e-mails cost the same PBKDF2 time.
_DUMMY_HASH = make_password(secrets.token_urlsafe(24))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for(token: str) -> str:
    """CSRF value derivable only from the HttpOnly session token."""
    return sha256_hex("spps-portal-csrf:" + token)


def password_problem(password: str, email: str) -> str | None:
    if len(password) < 12:
        return "Password must be at least 12 characters"
    if len(password) > 200:
        return "Password is too long"
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(password)) < 5:
        return "Choose a less common password"
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in lowered:
        return "Password must not contain your email name"
    return None


# ------------------------------------------------------------------ delivery

class FileOutbox:
    """LOCAL DEVELOPMENT ONLY: appends messages as JSON lines for tests/devs."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def send(self, kind: str, to: str, token: str | None = None) -> None:
        record = {"kind": kind, "to": to, "token": token,
                  "at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")


class ProviderOutbox:
    """Account e-mails (verification, reset, duplicate-registration notice) through a
    server.notifications delivery provider. The token appears ONLY inside the link to
    the recipient's own address; nothing clinical is ever included."""

    TEMPLATES = {
        "verify-email": ("Confirm your SPPS email address",
                         "Confirm your email address to finish creating your SPPS account:\n{link}\n\n"
                         "The link expires in 24 hours. If you did not create an account, ignore this message."),
        "password-reset": ("Reset your SPPS password",
                           "Use this link to choose a new password:\n{link}\n\nIt expires in 30 minutes and signs "
                           "out all devices. If you did not ask for this, ignore this message — your password is unchanged."),
        "guest-confirm": ("Confirm your appointment request",
                          "You asked for an appointment through SPPS. Open this link and press the confirm button to "
                          "send your request to the practitioner:\n{link}\n\nThe link expires in 24 hours. If you "
                          "did not ask for an appointment, ignore this message and nothing will be sent."),
        "existing-account-notice": ("SPPS sign-in help",
                                    "Someone tried to create an SPPS account with this email address, which already has "
                                    "an account. If this was you, sign in or reset your password at:\n{link}"),
    }

    def __init__(self, provider, base_url: str):
        self.provider, self.base = provider, base_url.rstrip("/")

    def send(self, kind: str, to: str, token: str | None = None) -> None:
        from urllib.parse import quote
        from .notifications import Message
        subject, body = self.TEMPLATES[kind]
        param = {"verify-email": "verify", "password-reset": "reset", "guest-confirm": "confirm"}.get(kind)
        page = "/find" if kind == "guest-confirm" else "/portal"
        link = f"{self.base}{page}" + (f"?{param}={quote(token, safe='')}" if param and token else "")
        self.provider.send(Message(to, subject, body.format(link=link)))


# ------------------------------------------------------------------ schemas

class RegisterIn(BaseModel):
    email: str = Field(min_length=6, max_length=250)
    password: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["athlete", "guardian"]
    adult_attested: bool
    terms_accepted: bool


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=250)


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=250)
    password: str = Field(min_length=1, max_length=200)


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


# ------------------------------------------------------------------ wiring

def _check_identity_role(engine, expected: str, database: str = "spps_dev_identity") -> None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, current_database() "
            "FROM pg_roles WHERE rolname=current_user")).one()
        memberships = conn.scalar(text("SELECT count(*) FROM pg_auth_members "
                                       "WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"))
    if row[0] != expected or any(row[1:5]) or row[5] != database or memberships:
        raise RuntimeError("Portal identity database role is unsafe")


def register_portal_routes(app: FastAPI, *, mode: str) -> None:
    from . import hosted_profile
    from .identity import require_local_database

    auth_url = os.getenv("SPPS_IDENTITY_AUTH_DATABASE_URL", "")
    portal_url = os.getenv("SPPS_IDENTITY_PORTAL_DATABASE_URL", "")
    if not auth_url or not portal_url:
        raise RuntimeError("Portal requires SPPS_IDENTITY_AUTH_DATABASE_URL and SPPS_IDENTITY_PORTAL_DATABASE_URL")
    hosted = mode != "local"
    if hosted and not hosted_profile.enabled():
        raise RuntimeError("Hosted portal authentication requires the hosted RLS profile (SPPS_HOSTED_RLS=true)")
    auth_args: dict = {}
    portal_args: dict = {}
    if hosted:
        # Re-validated here as well as in create_app: TLS, exact role and the separate identity database.
        identity_db = hosted_profile.database_names()[1]
        hosted_profile.validate_portal(os.environ, identity_db)
        auth_target = hosted_profile.require_hosted_database(auth_url, role=hosted_profile.IDENTITY_AUTH_ROLE,
                                                             database=identity_db)
        portal_target = hosted_profile.require_hosted_database(portal_url, role=hosted_profile.IDENTITY_PORTAL_ROLE,
                                                               database=identity_db)
        auth_url = auth_target.render_as_string(hide_password=False)
        portal_url = portal_target.render_as_string(hide_password=False)
        auth_args = {"connect_args": hosted_profile.connect_args(auth_target)}
        portal_args = {"connect_args": hosted_profile.connect_args(portal_target)}
    else:
        identity_db = "spps_dev_identity"
        for url in (auth_url, portal_url):
            require_local_database(url)
    outbox_path = os.getenv("SPPS_PORTAL_DEV_OUTBOX", "")
    if outbox_path and hosted:
        raise RuntimeError("SPPS_PORTAL_DEV_OUTBOX is local-only")
    if outbox_path:
        outbox = FileOutbox(Path(outbox_path))  # local development/tests: tokens readable by the test harness
    else:
        from .notifications import provider_from_env
        outbox = ProviderOutbox(provider_from_env(mode), os.getenv("SPPS_PUBLIC_BASE_URL", "http://localhost:8000"))
    app.state.portal_outbox = outbox
    auth_engine = create_engine(auth_url, pool_pre_ping=True, pool_size=3, max_overflow=2, **auth_args)
    portal_engine = create_engine(portal_url, pool_pre_ping=True, pool_size=3, max_overflow=2, **portal_args)
    _check_identity_role(auth_engine, "spps_identity_auth", identity_db)
    _check_identity_role(portal_engine, "spps_identity_portal", identity_db)
    app.state.portal_auth_engine = auth_engine
    app.state.portal_engine = portal_engine
    secure_cookie = os.getenv("MODEL_C_COOKIE_SECURE", "false").lower() == "true"
    idle_seconds = int(os.getenv("SPPS_PORTAL_IDLE_SECONDS", "1800"))

    def fn(name: str, **params):
        placeholders = ",".join(f":{k}" for k in params)
        with auth_engine.begin() as conn:
            return conn.execute(text(f"SELECT * FROM spps_identity_dev.{name}({placeholders})"), params).fetchall()

    def one(name: str, **params):
        rows = fn(name, **params)
        return rows[0][0] if rows else None

    def throttle(kind: str, subject: str, limit: int, window: int) -> None:
        if not one("auth_throttle_hit", p_key=sha256_hex(f"portal:{kind}:{subject}"),
                   p_window_seconds=window, p_limit=limit):
            raise HTTPException(429, "Too many attempts; try again later")

    def client_ip(request: Request) -> str:
        return hosted_profile.client_ip(request)

    def current_session(request: Request):
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 200:
            raise HTTPException(401, "Sign in required")
        rows = fn("auth_session", p_token_hash=sha256_hex(token), p_idle_seconds=idle_seconds)
        if not rows:
            raise HTTPException(401, "Session expired")
        s = rows[0]
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("x-csrf-token", "")
            if not sent or not secrets.compare_digest(sha256_hex(sent), s.csrf_hash):
                raise HTTPException(403, "CSRF validation failed")
        return {"token": token, "session_id": s.session_id, "account_id": s.account_id,
                "person_id": s.person_id, "display_name": s.display_name, "email": s.email,
                "roles": list(s.roles), "expires_at": s.expires_at}

    def set_cookie(response: Response, token: str) -> None:
        response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie, samesite="strict",
                            max_age=SESSION_TTL_SECONDS, path=COOKIE_PATH)

    def account_view(s: dict, token: str) -> dict:
        return {"account_id": s["account_id"], "person_id": s.get("person_id"),
                "display_name": s["display_name"], "email": s["email"],
                "roles": [r.lower() for r in s["roles"]], "csrf": csrf_for(token),
                "expires_at": s["expires_at"].isoformat() if s["expires_at"] else None}

    def db_guard(call):
        try:
            return call()
        except DBAPIError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", "") == "22023":
                raise HTTPException(422, "Invalid details") from None
            raise

    @app.post("/api/portal/auth/register", status_code=202)
    def portal_register(payload: RegisterIn, request: Request):
        throttle("register-ip", client_ip(request), 10, 3600)
        email = payload.email.strip().lower()
        if not payload.terms_accepted:
            raise HTTPException(422, "Accept the terms and privacy notice to register")
        if not payload.adult_attested:
            raise HTTPException(422, "Self-registration is for adults (18+). A parent or guardian must "
                                     "register to manage an account for a minor.")
        problem = password_problem(payload.password, email)
        if problem:
            raise HTTPException(422, problem)
        challenge = secrets.token_urlsafe(32)
        account = db_guard(lambda: one(
            "auth_register", p_email=email, p_password_hash=make_password(payload.password),
            p_display_name=payload.display_name.strip(), p_role=payload.role.upper(),
            p_adult_attested=True, p_challenge_hash=sha256_hex(challenge),
            p_challenge_ttl_seconds=VERIFY_TTL_SECONDS))
        if account is None:
            outbox.send("existing-account-notice", email)
        else:
            outbox.send("verify-email", email, challenge)
        return ACCEPTED

    @app.post("/api/portal/auth/resend-verification", status_code=202)
    def portal_resend(payload: EmailIn, request: Request):
        email = payload.email.strip().lower()
        throttle("resend-ip", client_ip(request), 10, 3600)
        throttle("resend-email", email, 3, 3600)
        challenge = secrets.token_urlsafe(32)
        if one("auth_resend_verification", p_email=email, p_challenge_hash=sha256_hex(challenge),
               p_ttl=VERIFY_TTL_SECONDS):
            outbox.send("verify-email", email, challenge)
        return ACCEPTED

    @app.post("/api/portal/auth/verify-email")
    def portal_verify(payload: TokenIn, request: Request):
        throttle("verify-ip", client_ip(request), 30, 3600)
        if one("auth_verify_email", p_challenge_hash=sha256_hex(payload.token)) is None:
            raise HTTPException(400, "This verification link is invalid or has expired")
        return {"verified": True}

    @app.post("/api/portal/auth/login")
    def portal_login(payload: LoginIn, request: Request, response: Response):
        email = payload.email.strip().lower()
        throttle("login-ip", client_ip(request), 30, 600)
        throttle("login-email", email, 8, 900)
        rows = fn("auth_login_lookup", p_email=email)
        stored = rows[0].password_hash if rows else _DUMMY_HASH
        password_ok = verify_password(payload.password, stored)  # always runs PBKDF2
        if not rows or not password_ok or rows[0].account_status != "VERIFIED":
            raise HTTPException(401, GENERIC_LOGIN_ERROR)
        token = secrets.token_urlsafe(36)
        if not one("auth_create_session", p_account_id=rows[0].account_id, p_password_hash_seen=stored,
                   p_token_hash=sha256_hex(token), p_csrf_hash=sha256_hex(csrf_for(token)),
                   p_ttl_seconds=SESSION_TTL_SECONDS):
            raise HTTPException(401, GENERIC_LOGIN_ERROR)
        set_cookie(response, token)
        s = fn("auth_session", p_token_hash=sha256_hex(token), p_idle_seconds=idle_seconds)[0]
        return account_view({"account_id": s.account_id, "person_id": s.person_id,
                             "display_name": s.display_name, "email": s.email,
                             "roles": list(s.roles), "expires_at": s.expires_at}, token)

    @app.get("/api/portal/auth/me")
    def portal_me(session=Depends(current_session)):
        return account_view(session, session["token"])

    @app.post("/api/portal/auth/logout")
    def portal_logout(response: Response, session=Depends(current_session)):
        one("auth_revoke_session", p_token_hash=sha256_hex(session["token"]))
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return {"ok": True}

    @app.post("/api/portal/auth/logout-all")
    def portal_logout_all(response: Response, session=Depends(current_session)):
        count = one("auth_revoke_all_sessions", p_token_hash=sha256_hex(session["token"]))
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return {"ok": True, "revoked_sessions": count}

    @app.post("/api/portal/auth/password-reset/request", status_code=202)
    def portal_reset_request(payload: EmailIn, request: Request):
        email = payload.email.strip().lower()
        throttle("reset-ip", client_ip(request), 10, 3600)
        throttle("reset-email", email, 3, 3600)
        challenge = secrets.token_urlsafe(32)
        if one("auth_request_password_reset", p_email=email, p_challenge_hash=sha256_hex(challenge),
               p_ttl=RESET_TTL_SECONDS):
            outbox.send("password-reset", email, challenge)
        return ACCEPTED

    @app.post("/api/portal/auth/password-reset/confirm")
    def portal_reset_confirm(payload: ResetIn, request: Request):
        throttle("reset-confirm-ip", client_ip(request), 20, 3600)
        problem = password_problem(payload.new_password, "")
        if problem:
            raise HTTPException(422, problem)
        if not one("auth_reset_password", p_challenge_hash=sha256_hex(payload.token),
                   p_new_hash=make_password(payload.new_password)):
            raise HTTPException(400, "This reset link is invalid or has expired")
        return {"reset": True, "message": "Password updated. All existing sessions were signed out."}

    @app.post("/api/portal/auth/password/change")
    def portal_change_password(payload: ChangeIn, request: Request, session=Depends(current_session)):
        throttle("change-account", str(session["account_id"]), 5, 900)
        problem = password_problem(payload.new_password, session["email"])
        if problem:
            raise HTTPException(422, problem)
        rows = fn("auth_login_lookup", p_email=session["email"])
        if not rows or not verify_password(payload.current_password, rows[0].password_hash):
            raise HTTPException(403, "Current password is incorrect")
        if not one("auth_change_password", p_token_hash=sha256_hex(session["token"]),
                   p_old_hash_seen=rows[0].password_hash, p_new_hash=make_password(payload.new_password)):
            raise HTTPException(409, "Password could not be changed; sign in again")
        return {"changed": True}

    def portal_query(token: str, sql: str, params: dict | None = None):
        """Run a read under the portal role with the session bound by RLS."""
        with portal_engine.begin() as conn:
            conn.execute(text("SELECT set_config('spps_identity.session_token', :t, true)"), {"t": token})
            return conn.execute(text(sql), params or {}).fetchall()

    app.state.portal_query = portal_query
    app.state.portal_current_session = current_session
    from .portal_guardian import register_guardian_routes
    register_guardian_routes(app)

    @app.get("/api/portal/me")
    def portal_profile(session=Depends(current_session)):
        token = session["token"]
        account = portal_query(token, "SELECT id, display_name, status, verified_at "
                                      "FROM spps_identity_dev.platform_accounts")
        roles = portal_query(token, "SELECT role FROM spps_identity_dev.platform_role_grants "
                                    "WHERE status='ACTIVE' AND revoked_at IS NULL ORDER BY role")
        guardianships = portal_query(token, "SELECT id, dependent_person_id, status, scope, expires_at "
                                            "FROM spps_identity_dev.guardian_authorities ORDER BY id")
        links = portal_query(token, "SELECT id, person_id, practitioner_id, status "
                                    "FROM spps_identity_dev.practitioner_athlete_links ORDER BY id")
        if len(account) != 1:
            raise HTTPException(401, "Session expired")
        return {"account": {"id": account[0].id, "display_name": account[0].display_name,
                            "status": account[0].status},
                "roles": [r.role.lower() for r in roles],
                "guardian_authorities": [{"id": g.id, "dependent_person_id": g.dependent_person_id,
                                          "status": g.status, "scope": g.scope} for g in guardianships],
                "practitioner_links": [{"id": l.id, "person_id": l.person_id,
                                        "practitioner_id": l.practitioner_id, "status": l.status}
                                       for l in links]}
