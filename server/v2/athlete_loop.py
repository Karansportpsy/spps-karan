"""Phase 6: the practitioner-invited athlete loop (flags SPPS_PORTAL_ENABLED and SPPS_ATHLETE_LOOP_ENABLED).

Practitioner side (practitioner session): invite an athlete they hold a record for, assign an assignable
instrument or a written programme, set check-ins and alert rules, release summaries, end the link.
Portal side (athlete/guardian session, cookie ``spps_loop``): register, verify e-mail, sign in, see the consent
screen, accept, complete assignments and check-ins, end the link, export own data. Under-18: the guardian is invited
first and records consent; then the athlete is invited. The guardian sees participation and released summaries
only, unless BOTH the practitioner and the athlete enable more.

All state is on the server (invitations, consent, links), so every e-mailed link works in any browser or in Chrome
from the Android app. Portal routes use only the identity-schema connections; they never touch clinical tables.
Every portal response carries the not-a-crisis-service notice and the owner's crisis contacts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from ..models import now
from ..security import make_password, verify_password
from . import guards
from .context import Ctx, require_admin
from .identity_models import (SCHEMA, AthleteAccount, AthleteSession, LoopAlert, LoopAlertRule, LoopAssignment,
                              LoopCheckin, LoopCheckinPlan, LoopInvite, LoopLink, LoopSettings, LoopSharedSummary,
                              LoopSubmission, V2IdentityBase)

LOG = logging.getLogger("spps.loop")
COOKIE = "spps_loop"
CHECKIN_ITEMS = {"stress": "Stress", "confidence": "Confidence", "anxiety": "Anxiety", "mood": "Mood",
                 "motivation": "Motivation"}
NOT_CRISIS = ("SPPS is not a crisis or emergency service. If you are in danger or need urgent help, use the "
              "contacts below.")
INVITE_TTL = timedelta(days=14)
SESSION_TTL = timedelta(hours=12)
DEFAULT_SCOPE = ["Your name as your practitioner records it", "Assessments and programmes you complete here",
                 "Your check-in answers and when a check-in was missed",
                 "Alerts raised by the rules your practitioner sets"]


def sha(v: str) -> str:
    return hashlib.sha256(("spps-loop:" + v).encode()).hexdigest()


def _aware(v):
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


# ------------------------------------------------------------------ connections
class Identity:
    """auth: pre-authentication (accounts, sessions, invites); data: loop tables under row-level policies."""

    def __init__(self, app, rls: bool):
        if rls:
            auth_url = os.environ["SPPS_IDENTITY_AUTH_DATABASE_URL"]
            data_url = os.environ["SPPS_IDENTITY_PORTAL_DATABASE_URL"]
            self.auth_engine = _identity_engine(auth_url)
            self.data_engine = _identity_engine(data_url)
            self.translate = None
        else:
            base = app.state.engine.execution_options(schema_translate_map={SCHEMA: None})
            self.auth_engine = self.data_engine = base
            self.translate = True
        self.Auth = sessionmaker(bind=self.auth_engine, expire_on_commit=False)
        self.Data = sessionmaker(bind=self.data_engine, expire_on_commit=False)
        for factory in (self.Auth, self.Data):
            @event.listens_for(factory, "after_begin")
            def _bind(session, transaction, connection):
                if connection.dialect.name != "postgresql":
                    return
                who = session.info.get("loop", {})
                connection.execute(text(
                    "SELECT set_config('spps.loop_account_id', :a, true), "
                    "set_config('spps.practitioner_id', :p, true), set_config('spps.tenant_id', :t, true)"),
                    {"a": str(who.get("account", "")), "p": str(who.get("practitioner", "")),
                     "t": str(who.get("tenant", ""))})

    def data(self, **who):
        s = self.Data()
        s.info["loop"] = who
        return s


def _identity_engine(url):
    from ..db import make_engine
    if url.startswith("postgresql") and os.getenv("MODEL_C_ENV", "local") == "local":
        from ..identity import require_local_database
        require_local_database(url)
        u = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
        return create_engine(u, connect_args={"options": f"-c search_path={SCHEMA}"}, pool_pre_ping=True,
                             pool_size=2, max_overflow=2)
    from .. import hosted_profile
    parsed = hosted_profile.normalize(url)
    return create_engine(parsed.render_as_string(hide_password=False),
                         connect_args=hosted_profile.connect_args(parsed, schema=SCHEMA), pool_pre_ping=True,
                         pool_size=1, max_overflow=1)


def create_local_tables(engine) -> None:
    V2IdentityBase.metadata.create_all(engine.execution_options(schema_translate_map={SCHEMA: None}))


# ------------------------------------------------------------------ mail (SMTP)
def send_mail(app, to: str, subject: str, body: str) -> bool:
    return _deliver(app, to, subject, body)


def _deliver(app, to: str, subject: str, body: str) -> bool:
    host, user_, pw, sender = (os.getenv(k, "") for k in ("SPPS_SMTP_HOST", "SPPS_SMTP_USERNAME",
                                                          "SPPS_SMTP_PASSWORD", "SPPS_SMTP_FROM"))
    if not all((host, user_, pw, sender)):
        LOG.warning("loop e-mail not sent: mail not configured (subject=%s)", subject)
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.set_content(body + "\n\n" + NOT_CRISIS + "\nSPPS by WinMindPerform\n")
    try:
        port = int(os.getenv("SPPS_SMTP_PORT", "587"))
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(host, port, timeout=10) as c:
            if port != 465:
                c.starttls()
            c.login(user_, pw)
            c.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        LOG.warning("loop e-mail not sent: smtp error (subject=%s)", subject)
        return False


def base_url(request: Request) -> str:
    return os.getenv("SPPS_PUBLIC_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


# ------------------------------------------------------------------ check-in schedule
def due_dates(plan: LoopCheckinPlan, today: date) -> list[str]:
    start = date.fromisoformat(plan.starts_on)
    step = 1 if plan.frequency == "daily" else 7
    out, d = [], start
    while d <= today and len(out) < 400:
        out.append(d.isoformat())
        d += timedelta(days=step)
    return out


def evaluate(rules, answers: dict, urgent: bool) -> list[tuple[str, bool]]:
    """Transparent rules: each rule that matches raises an alert. An urgent answer always raises an urgent alert,
    whatever the scores."""
    out = []
    if urgent:
        out.append(("The athlete asked for urgent contact", True))
    for r in rules:
        v = answers.get(r.item)
        if v is None:
            continue
        if (r.op == ">=" and v >= r.threshold) or (r.op == "<=" and v <= r.threshold):
            out.append((f"{CHECKIN_ITEMS.get(r.item, r.item)} {v:g} {r.op} {r.threshold:g}", False))
    return out


# ------------------------------------------------------------------ request models
class InviteIn(BaseModel):
    email: EmailStr | None = None  # athlete e-mail (adults) or guardian e-mail (under-18; defaults to the profile)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=2, max_length=120)
    role: str
    terms_accepted: bool


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class AcceptIn(BaseModel):
    consent: bool
    guardian_evidence: str = Field(default="", max_length=200)  # guardian types their full name as signature
    guardian_declaration: bool = False  # "I am the parent or lawful guardian and may consent for this athlete"
    athlete_email: EmailStr | None = None  # guardian provides the athlete's e-mail


class AssignIn(BaseModel):
    kind: str
    title: str = Field(min_length=2, max_length=160)
    instrument_id: int | None = None
    tasks: list[str] = Field(default_factory=list, max_length=30)
    schedule: str = Field(default="", max_length=160)
    due_on: date | None = None


class SubmitIn(BaseModel):
    answers: dict[str, float | str | bool]


class PlanIn(BaseModel):
    frequency: str = "weekly"
    items: list[str] = Field(min_length=3, max_length=5)
    starts_on: date | None = None


class RuleIn(BaseModel):
    item: str
    op: str
    threshold: float = Field(ge=0, le=10)


class CheckinIn(BaseModel):
    answers: dict[str, float]
    urgent: bool = False


class SummaryIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=2, max_length=6000)
    for_guardian: bool = True


class ToggleIn(BaseModel):
    enabled: bool


class CrisisIn(BaseModel):
    contacts: list[dict] = Field(min_length=1, max_length=10)


_CURRENT: dict = {}
EXPORT_EXTRAS: list = []  # phase 8 adds enquiries and report-sharing decisions to the athlete's export


def register(app: FastAPI, ctx: Ctx) -> None:
    ident = Identity(app, ctx.rls)
    _CURRENT["identity"] = ident  # the timeline hook below is module-level; it always uses the live app
    app.state.v2_identity = ident
    get_current, db_session, athlete_for = ctx.get_current, ctx.db_session, ctx.athlete_for

    def crisis(tenant_id: int) -> list:
        with ident.Auth() as a:
            s = a.get(LoopSettings, tenant_id)
            return json.loads(s.crisis_contacts) if s else []

    def wrap(tenant_id: int, body):
        return {"notice": NOT_CRISIS, "crisis_contacts": crisis(tenant_id), "data": body}

    # ------------------------------------------------------------------ portal session
    def portal_account(request: Request) -> AthleteAccount:
        raw = request.cookies.get(COOKIE, "")
        if not raw or len(raw) > 200:
            raise HTTPException(401, "Sign in required")
        with ident.Auth() as a:
            s = a.scalar(select(AthleteSession).where(AthleteSession.token_hash == sha(raw),
                                                      AthleteSession.revoked == False))  # noqa: E712
            if not s or _aware(s.expires_at) < now():
                raise HTTPException(401, "Session expired")
            acc = a.get(AthleteAccount, s.account_id)
            if not acc or acc.status != "active":
                raise HTTPException(401, "Account not active")
            if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), s.csrf_token):
                raise HTTPException(403, "CSRF validation failed")
            a.expunge(acc)
            return acc

    def tenant_id_default() -> int:
        from ..models import Tenant
        with app.state.DB() as db:
            t = db.scalar(select(Tenant).where(Tenant.slug == os.getenv("MODEL_C_PUBLIC_TENANT", "model-c-demo")))
            return t.id if t else 0

    # ------------------------------------------------------------------ practitioner side
    def my_link(d, user, link_id) -> LoopLink:
        l_ = d.scalar(select(LoopLink).where(LoopLink.id == link_id, LoopLink.practitioner_id == user.id,
                                             LoopLink.tenant_id == user.tenant_id))
        if not l_:
            raise HTTPException(404, "Link not found")
        return l_

    def pdata(user):
        return ident.data(practitioner=user.id, tenant=user.tenant_id)

    @app.post("/api/v2/loop/athletes/{athlete_id}/invite", status_code=201)
    def invite(athlete_id: int, body: InviteIn, request: Request, user=Depends(get_current), db=Depends(db_session)):
        return create_invite(db, user, athlete_for(db, user, athlete_id), body.email, request)

    def create_invite(db, user, a, given_email, request) -> dict:
        """Open a link for athlete record ``a`` and send the first invitation (guardian first for under-18s).
        Also used by phase 8 when a practitioner accepts a directory enquiry."""
        from .models import AthleteProfile
        from .record import is_minor
        prof = db.get(AthleteProfile, a.id)
        minor = bool(prof and is_minor(prof.age_group))
        email = str(given_email or (prof.guardian_email if (minor and prof) else "") or "").strip().lower()
        if not email:
            raise HTTPException(422, "Give the " + ("guardian's" if minor else "athlete's") + " e-mail address")
        if not a.service_consent_recorded_at:
            raise HTTPException(409, "Record service consent before inviting the athlete")
        with pdata(user) as d:
            if d.scalar(select(LoopLink.id).where(LoopLink.athlete_record_id == a.id, LoopLink.practitioner_id == user.id,
                                                  LoopLink.status != "ended")):
                raise HTTPException(409, "This athlete already has an open link")
            link = LoopLink(tenant_id=user.tenant_id, practitioner_id=user.id, practitioner_name=user.name,
                            practitioner_email=user.email, athlete_record_id=a.id, athlete_label=a.display_name,
                            is_minor=minor, status="invited", scope=json.dumps(DEFAULT_SCOPE))
            d.add(link)
            d.flush()
            token = secrets.token_urlsafe(32)
            inv = LoopInvite(tenant_id=user.tenant_id, practitioner_id=user.id, link_id=link.id, email=email,
                             kind="guardian" if minor else "athlete", token_hash=sha(token),
                             expires_at=now() + INVITE_TTL)
            d.add(inv)
            d.commit()
            link_id, kind = link.id, inv.kind
        url = f"{base_url(request)}/athlete?invite={token}"
        send_mail(app, email, "Your sport psychologist has invited you to SPPS",
                  f"{user.name} has invited you to join SPPS as {'the guardian of ' + a.display_name if kind == 'guardian' else a.display_name}.\n"
                  f"Open this link (it works in any browser or the SPPS app): {url}\nIt expires in 14 days.")
        ctx.event(db, user, "loop.invited", "athlete", a.id)
        db.commit()
        return {"link_id": link_id, "kind": kind, "url": url, "expires_in_days": INVITE_TTL.days}

    def link_json(d, l_):
        return {"id": l_.id, "athlete_record_id": l_.athlete_record_id, "athlete_label": l_.athlete_label,
                "status": l_.status, "is_minor": l_.is_minor, "athlete_consent_at": l_.athlete_consent_at,
                "guardian_consent_at": l_.guardian_consent_at, "ended_at": l_.ended_at, "ended_by": l_.ended_by,
                # The practitioner checks the guardian's typed signature against their own records.
                "guardian_signature": (l_.guardian_consent_evidence or "").removeprefix("typed-signature:") or None,
                "guardian_detail": {"practitioner": l_.guardian_detail_by_practitioner,
                                    "athlete": l_.guardian_detail_by_athlete},
                "open_alerts": len(d.scalars(select(LoopAlert).where(LoopAlert.link_id == l_.id,
                                                                    LoopAlert.acknowledged_at.is_(None))).all())}

    @app.get("/api/v2/loop/links")
    def my_links(user=Depends(get_current)):
        with pdata(user) as d:
            return [link_json(d, l_) for l_ in d.scalars(select(LoopLink).where(LoopLink.practitioner_id == user.id)
                                                         .order_by(LoopLink.id.desc()))]

    @app.post("/api/v2/loop/links/{link_id}/end")
    def practitioner_end(link_id: int, user=Depends(get_current)):
        with pdata(user) as d:
            l_ = my_link(d, user, link_id)
            if l_.status != "ended":
                l_.status, l_.ended_at, l_.ended_by = "ended", now(), "practitioner"
                d.commit()
            return link_json(d, l_)

    def end_links_on_closure(db, user):
        from .identity_models import LoopShareRequest
        with pdata(user) as d:
            links = d.scalars(select(LoopLink).where(LoopLink.practitioner_id == user.id,
                                                     LoopLink.status != "ended")).all()
            for l_ in links:  # revoke shares first: only a practitioner on an open link may revoke
                for s in d.scalars(select(LoopShareRequest).where(LoopShareRequest.link_id == l_.id,
                                                                  LoopShareRequest.status.in_(("pending", "approved")))):
                    s.status = "revoked"
            d.flush()
            for l_ in links:
                l_.status, l_.ended_at, l_.ended_by = "ended", now(), "practitioner"
            d.commit()
    from .trust import add_closure_hook
    add_closure_hook(app, end_links_on_closure)

    def active_link(d, user, link_id) -> LoopLink:
        l_ = my_link(d, user, link_id)
        if l_.status != "active":
            raise HTTPException(409, "The link is not active")
        return l_

    @app.post("/api/v2/loop/links/{link_id}/assignments", status_code=201)
    def assign(link_id: int, body: AssignIn, user=Depends(get_current), db=Depends(db_session)):
        if body.kind == "instrument":
            from .assessments import instrument_json, visible_instrument
            inst = visible_instrument(db, user, body.instrument_id or 0)
            if not inst.assignable_to_athlete or inst.licence == "licensed_score_entry_only":
                raise HTTPException(409, "This instrument is not marked assignable to athletes")
            payload = instrument_json(db, inst)
        elif body.kind == "programme":
            tasks = [t.strip() for t in body.tasks if t.strip()]
            if not tasks:
                raise HTTPException(422, "Write at least one task")
            payload = {"tasks": tasks}
        else:
            raise HTTPException(422, "Assign an instrument or a programme")
        with pdata(user) as d:
            l_ = active_link(d, user, link_id)
            a = LoopAssignment(link_id=l_.id, kind=body.kind, title=body.title.strip(), payload=json.dumps(payload),
                               schedule=body.schedule, due_on=body.due_on.isoformat() if body.due_on else "")
            d.add(a)
            d.commit()
            return {"id": a.id}

    @app.put("/api/v2/loop/links/{link_id}/checkin-plan")
    def checkin_plan(link_id: int, body: PlanIn, user=Depends(get_current)):
        if body.frequency not in ("weekly", "daily") or set(body.items) - set(CHECKIN_ITEMS) or \
                len(set(body.items)) != len(body.items):
            raise HTTPException(422, "Choose weekly or daily and 3-5 of: " + ", ".join(CHECKIN_ITEMS))
        with pdata(user) as d:
            l_ = active_link(d, user, link_id)
            p = d.get(LoopCheckinPlan, l_.id) or LoopCheckinPlan(link_id=l_.id)
            p.frequency, p.items, p.active = body.frequency, json.dumps(body.items), True
            p.starts_on = (body.starts_on or date.today()).isoformat()
            d.merge(p)
            d.commit()
            return {"frequency": p.frequency, "items": body.items, "starts_on": p.starts_on}

    @app.post("/api/v2/loop/links/{link_id}/alert-rules", status_code=201)
    def alert_rule(link_id: int, body: RuleIn, user=Depends(get_current)):
        if body.item not in CHECKIN_ITEMS or body.op not in (">=", "<="):
            raise HTTPException(422, "Choose a check-in item and >= or <=")
        with pdata(user) as d:
            l_ = my_link(d, user, link_id)
            r = LoopAlertRule(link_id=l_.id, item=body.item, op=body.op, threshold=body.threshold)
            d.add(r)
            d.commit()
            return {"id": r.id}

    @app.post("/api/v2/loop/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: int, user=Depends(get_current)):
        with pdata(user) as d:
            al = d.get(LoopAlert, alert_id)
            if not al:
                raise HTTPException(404, "Alert not found")
            my_link(d, user, al.link_id)
            al.acknowledged_at = al.acknowledged_at or now()
            d.commit()
            return {"acknowledged_at": al.acknowledged_at}

    @app.post("/api/v2/loop/links/{link_id}/summaries", status_code=201)
    def release_summary(link_id: int, body: SummaryIn, user=Depends(get_current)):
        with pdata(user) as d:
            l_ = active_link(d, user, link_id)
            s = LoopSharedSummary(link_id=l_.id, title=body.title, body=body.body, for_guardian=body.for_guardian)
            d.add(s)
            d.commit()
            return {"id": s.id}

    @app.post("/api/v2/loop/links/{link_id}/guardian-detail")
    def practitioner_guardian_detail(link_id: int, body: ToggleIn, user=Depends(get_current)):
        with pdata(user) as d:
            l_ = my_link(d, user, link_id)
            l_.guardian_detail_by_practitioner = body.enabled
            d.commit()
            return link_json(d, l_)

    def activity(d, l_):
        plan = d.get(LoopCheckinPlan, l_.id)
        checkins = {c.due_on: c for c in d.scalars(select(LoopCheckin).where(LoopCheckin.link_id == l_.id))}
        dues = due_dates(plan, date.today()) if plan and plan.active else []
        return {"assignments": [{"id": a.id, "kind": a.kind, "title": a.title, "status": a.status,
                                 "due_on": a.due_on, "completed_at": a.completed_at,
                                 "submissions": [{"submitted_at": s.submitted_at, "answers": json.loads(s.answers),
                                                  "scores": json.loads(s.scores)} for s in d.scalars(
                                     select(LoopSubmission).where(LoopSubmission.assignment_id == a.id))]}
                                for a in d.scalars(select(LoopAssignment).where(LoopAssignment.link_id == l_.id))],
                "checkins": [{"due_on": due, "status": "submitted" if due in checkins else "missing",
                              "answers": json.loads(checkins[due].answers) if due in checkins else None,
                              "urgent": checkins[due].urgent if due in checkins else False} for due in dues],
                "plan": {"frequency": plan.frequency, "items": json.loads(plan.items)} if plan else None,
                "alerts": [{"id": x.id, "reason": x.reason, "urgent": x.urgent, "created_at": x.created_at,
                            "acknowledged_at": x.acknowledged_at} for x in d.scalars(
                    select(LoopAlert).where(LoopAlert.link_id == l_.id).order_by(LoopAlert.id.desc()))]}

    @app.get("/api/v2/loop/links/{link_id}/activity")
    def link_activity(link_id: int, user=Depends(get_current)):
        with pdata(user) as d:
            return activity(d, my_link(d, user, link_id))

    @app.get("/api/v2/admin/crisis-contacts")
    def get_crisis(user=Depends(get_current)):
        require_admin(user)
        return crisis(user.tenant_id)

    @app.put("/api/v2/admin/crisis-contacts")
    def put_crisis(body: CrisisIn, user=Depends(get_current)):
        require_admin(user)
        clean = [{"name": str(c.get("name", ""))[:120], "phone": str(c.get("phone", ""))[:40],
                  "hours": str(c.get("hours", ""))[:80]} for c in body.contacts if c.get("name") and c.get("phone")]
        if not clean:
            raise HTTPException(422, "Each contact needs a name and a phone number")
        with ident.Auth() as a:
            s = a.get(LoopSettings, user.tenant_id) or LoopSettings(tenant_id=user.tenant_id)
            s.crisis_contacts, s.updated_at = json.dumps(clean), now()
            a.merge(s)
            a.commit()
        return clean

    @app.post("/api/v2/admin/loop/send-reminders")
    def send_reminders(request: Request, user=Depends(get_current)):
        """Owner (or a scheduled job calling this as the owner): e-mail athletes whose latest check-in is due."""
        require_admin(user)
        sent = 0
        with ident.Auth() as a:
            due = _due_reminders_orm(a)
        for row in due:
            if send_mail(app, row["email"], "Your SPPS check-in is due",
                         f"Your check-in for {row['due_on']} is waiting: {base_url(request)}/athlete"):
                sent += 1
        return {"reminders_sent": sent}

    @app.get("/athlete")
    def athlete_page():
        from fastapi.responses import FileResponse
        from ..main import WEB
        # Invitation and verification links carry a token in the address: never cache or index this page.
        return FileResponse(WEB / "athlete.html", headers={"Cache-Control": "no-store",
                                                          "X-Robots-Tag": "noindex, nofollow"})

    # ------------------------------------------------------------------ portal: public + auth
    @app.get("/api/v2/portal/crisis-contacts")
    def public_crisis(request: Request):
        guards.limit_ip(request, "portal-auth")
        return {"notice": NOT_CRISIS, "crisis_contacts": crisis(tenant_id_default())}

    def find_invite(a, token) -> LoopInvite:
        inv = a.scalar(select(LoopInvite).where(LoopInvite.token_hash == sha(token)))
        if not inv or inv.used_at or inv.revoked_at or _aware(inv.expires_at) < now():
            raise HTTPException(404, "This invitation has expired or was already used")
        return inv

    @app.get("/api/v2/portal/invites/{token}")
    def invite_preview(token: str, request: Request):
        guards.limit_ip(request, "invite-redeem")
        with ident.Auth() as a:
            inv = find_invite(a, token)
            l_ = a.get(LoopLink, inv.link_id)
            return wrap(inv.tenant_id, {"kind": inv.kind, "email": inv.email, "practitioner_name": l_.practitioner_name,
                                        "athlete_label": l_.athlete_label, "is_minor": l_.is_minor,
                                        "scope": json.loads(l_.scope),
                                        "guardian_sees": "participation and summaries your practitioner releases; "
                                                         "your answers only if you and your practitioner both allow it"})

    @app.post("/api/v2/portal/auth/register", status_code=202)
    def portal_register(body: RegisterIn, request: Request):
        guards.limit_ip(request, "portal-auth")
        if not body.terms_accepted or body.role not in ("athlete", "guardian"):
            raise HTTPException(422, "Accept the terms and choose athlete or guardian")
        email = str(body.email).lower()
        token = secrets.token_urlsafe(32)
        with ident.Auth() as a:
            if not a.scalar(select(AthleteAccount.id).where(AthleteAccount.email == email)):
                a.add(AthleteAccount(email=email, password_hash=make_password(body.password),
                                     display_name=body.display_name.strip(), role=body.role, status="unverified",
                                     verify_token_hash=sha(token), verify_expires_at=now() + timedelta(days=2)))
                a.commit()
                send_mail(app, email, "Confirm your SPPS e-mail",
                          f"Confirm your e-mail (works in any browser or the app): {base_url(request)}/athlete?verify={token}")
        return {"accepted": True, "message": "Check your e-mail to confirm your address."}

    @app.post("/api/v2/portal/auth/verify")
    def portal_verify(body: TokenIn, request: Request):
        guards.limit_ip(request, "portal-auth")
        with ident.Auth() as a:
            acc = a.scalar(select(AthleteAccount).where(AthleteAccount.verify_token_hash == sha(body.token)))
            if not acc or _aware(acc.verify_expires_at) < now():
                raise HTTPException(400, "This link is invalid or has expired")
            acc.status, acc.verified_at, acc.verify_token_hash = "active", now(), None
            a.commit()
        return {"verified": True}

    @app.post("/api/v2/portal/auth/login")
    def portal_login(body: LoginIn, request: Request, response: Response):
        guards.limit_ip(request, "portal-auth")
        guards.hit("portal-auth", guards.email_key(str(body.email)))
        with ident.Auth() as a:
            acc = a.scalar(select(AthleteAccount).where(AthleteAccount.email == str(body.email).lower()))
            from ..main import DUMMY_PASSWORD_HASH
            ok = verify_password(body.password, acc.password_hash if acc else DUMMY_PASSWORD_HASH)
            if not acc or not ok or acc.status != "active":
                raise HTTPException(401, "Invalid e-mail or password, or the e-mail is not confirmed yet")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            a.add(AthleteSession(account_id=acc.id, token_hash=sha(token), csrf_token=csrf,
                                 expires_at=now() + SESSION_TTL))
            a.commit()
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/api/v2/portal",
                            secure=os.getenv("MODEL_C_COOKIE_SECURE", "false").lower() == "true",
                            max_age=int(SESSION_TTL.total_seconds()))
        return {"csrf": csrf, "role": acc.role, "display_name": acc.display_name}

    @app.post("/api/v2/portal/auth/logout")
    def portal_logout(request: Request, response: Response, acc=Depends(portal_account)):
        with ident.Auth() as a:
            a.query(AthleteSession).filter(AthleteSession.token_hash == sha(request.cookies.get(COOKIE, ""))).update(
                {AthleteSession.revoked: True}, synchronize_session=False)
            a.commit()
        response.delete_cookie(COOKIE, path="/api/v2/portal")
        return {"ok": True}

    @app.get("/api/v2/portal/me")
    def portal_me(acc=Depends(portal_account)):
        return wrap(tenant_id_default(), {"email": acc.email, "display_name": acc.display_name, "role": acc.role})

    # ------------------------------------------------------------------ portal: consent and the link
    def adata(acc):
        return ident.data(account=acc.id)

    @app.post("/api/v2/portal/invites/{token}/accept")
    def accept(token: str, body: AcceptIn, request: Request, acc=Depends(portal_account)):
        guards.limit_ip(request, "invite-redeem")
        if not body.consent:
            raise HTTPException(422, "Consent is required to link with this practitioner")
        with ident.Auth() as a:
            return accept_invite(a, find_invite(a, token), body, acc, request)

    def accept_invite(a, inv, body, acc, request):
        """Shared by the e-mailed link (token) and, in phase 8, the account's list of pending invitations."""
        if True:
            if inv.email != acc.email or inv.kind != acc.role:
                raise HTTPException(403, "This invitation is for another account")
            l_ = a.get(LoopLink, inv.link_id)
            if l_.status == "ended":
                raise HTTPException(409, "This link has ended")
            if inv.kind == "guardian":
                if len(body.guardian_evidence.strip()) < 3 or not body.athlete_email:
                    raise HTTPException(422, "Type your full name as your signature and give the athlete's e-mail")
                if not body.guardian_declaration:
                    raise HTTPException(422, "Confirm that you are the athlete's parent or lawful guardian and may "
                                             "give consent for them")
                if str(body.athlete_email).lower() == acc.email:
                    raise HTTPException(422, "Give the athlete's own e-mail address, not yours")
                l_.guardian_account_id, l_.guardian_consent_at = acc.id, now()
                l_.guardian_consent_evidence = ("typed-signature:" + body.guardian_evidence.strip()
                                                + "; declared parent or lawful guardian")[:240]
                l_.status = "guardian_consented"
                nxt = secrets.token_urlsafe(32)
                a.add(LoopInvite(tenant_id=inv.tenant_id, practitioner_id=inv.practitioner_id, link_id=l_.id,
                                 email=str(body.athlete_email).lower(), kind="athlete", token_hash=sha(nxt),
                                 expires_at=now() + INVITE_TTL))
                inv.used_at = now()
                a.commit()
                send_mail(app, str(body.athlete_email).lower(), "You have been invited to SPPS",
                          f"{l_.practitioner_name} has invited you, with your guardian's consent: "
                          f"{base_url(request)}/athlete?invite={nxt}")
                return wrap(inv.tenant_id, {"status": l_.status, "athlete_invited": True})
            if l_.is_minor and not l_.guardian_consent_at:
                raise HTTPException(409, "A guardian must consent first")
            l_.athlete_account_id, l_.athlete_consent_at, l_.status = acc.id, now(), "active"
            inv.used_at = now()
            a.commit()
            return wrap(inv.tenant_id, {"status": "active", "link_id": l_.id})

    def portal_link(d, acc, link_id, *, athlete_only=False) -> LoopLink:
        l_ = d.get(LoopLink, link_id)
        mine = l_ and (l_.athlete_account_id == acc.id or (not athlete_only and l_.guardian_account_id == acc.id))
        if not mine or l_.status == "ended":
            raise HTTPException(404, "Not found")
        return l_

    @app.get("/api/v2/portal/links")
    def portal_links(acc=Depends(portal_account)):
        with adata(acc) as d:
            rows = d.scalars(select(LoopLink).where(
                (LoopLink.athlete_account_id == acc.id) | (LoopLink.guardian_account_id == acc.id))).all()
            return wrap(tenant_id_default(), [{"id": l_.id, "practitioner_name": l_.practitioner_name,
                                               "athlete_label": l_.athlete_label, "status": l_.status,
                                               "you_are": "athlete" if l_.athlete_account_id == acc.id else "guardian",
                                               "guardian_detail_allowed_by_you": l_.guardian_detail_by_athlete}
                                              for l_ in rows])

    @app.post("/api/v2/portal/links/{link_id}/end")
    def portal_end(link_id: int, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id)
            l_.status, l_.ended_at = "ended", now()
            l_.ended_by = "athlete" if l_.athlete_account_id == acc.id else "guardian"
            d.commit()
            return wrap(l_.tenant_id, {"status": "ended"})

    @app.post("/api/v2/portal/links/{link_id}/guardian-detail")
    def athlete_guardian_detail(link_id: int, body: ToggleIn, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id, athlete_only=True)
            l_.guardian_detail_by_athlete = body.enabled
            d.commit()
            return wrap(l_.tenant_id, {"guardian_detail_by_athlete": body.enabled})

    @app.get("/api/v2/portal/links/{link_id}/assignments")
    def portal_assignments(link_id: int, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id, athlete_only=True)
            return wrap(l_.tenant_id, [{"id": a.id, "kind": a.kind, "title": a.title, "status": a.status,
                                        "due_on": a.due_on, "schedule": a.schedule, "content": json.loads(a.payload)}
                                       for a in d.scalars(select(LoopAssignment).where(LoopAssignment.link_id == l_.id,
                                                                                       LoopAssignment.status != "withdrawn"))])

    @app.post("/api/v2/portal/assignments/{assignment_id}/submit", status_code=201)
    def portal_submit(assignment_id: int, body: SubmitIn, acc=Depends(portal_account)):
        with adata(acc) as d:
            a = d.get(LoopAssignment, assignment_id)
            if not a:
                raise HTTPException(404, "Not found")
            l_ = portal_link(d, acc, a.link_id, athlete_only=True)
            if a.status != "assigned":
                raise HTTPException(409, "Already completed")
            scores = {}
            if a.kind == "instrument":
                from .assessments import score_items
                inst = json.loads(a.payload)
                ids = {str(i["id"]): i for i in inst["items"]}
                resp = {}
                for k, v in body.answers.items():
                    if k not in ids or not isinstance(v, (int, float)) or not inst["response_min"] <= v <= inst["response_max"]:
                        raise HTTPException(422, "Answer every question with a listed option")
                    resp[int(k)] = float(v)
                scores = score_items(inst["subscales"], inst["items"], resp, inst["response_min"], inst["response_max"])
            d.add(LoopSubmission(assignment_id=a.id, link_id=l_.id, answers=json.dumps(body.answers),
                                 scores=json.dumps(scores)))
            a.status, a.completed_at = "completed", now()
            d.commit()
            return wrap(l_.tenant_id, {"completed": True})

    @app.get("/api/v2/portal/links/{link_id}/checkins")
    def portal_checkins(link_id: int, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id, athlete_only=True)
            plan = d.get(LoopCheckinPlan, l_.id)
            done = {c.due_on for c in d.scalars(select(LoopCheckin).where(LoopCheckin.link_id == l_.id))}
            dues = due_dates(plan, date.today()) if plan and plan.active else []
            return wrap(l_.tenant_id, {"items": [{"key": k, "label": CHECKIN_ITEMS[k]} for k in json.loads(plan.items)]
                                       if plan else [], "frequency": plan.frequency if plan else None,
                                       "due": [{"due_on": x, "status": "submitted" if x in done else "missing"}
                                               for x in dues], "scale": "0 (not at all) to 10 (extremely)",
                                       "urgent_question": "I would like my practitioner to contact me urgently"})

    @app.post("/api/v2/portal/links/{link_id}/checkins", status_code=201)
    def portal_checkin(link_id: int, body: CheckinIn, request: Request, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id, athlete_only=True)
            plan = d.get(LoopCheckinPlan, l_.id)
            if not plan or not plan.active:
                raise HTTPException(409, "No check-in is set up")
            items = json.loads(plan.items)
            if set(body.answers) != set(items) or any(not 0 <= v <= 10 for v in body.answers.values()):
                raise HTTPException(422, "Answer each question from 0 to 10")
            dues = due_dates(plan, date.today())
            if not dues:
                raise HTTPException(409, "No check-in is due yet")
            due = dues[-1]
            if d.scalar(select(LoopCheckin.id).where(LoopCheckin.link_id == l_.id, LoopCheckin.due_on == due)):
                raise HTTPException(409, "This check-in is already done")
            c = LoopCheckin(link_id=l_.id, due_on=due, answers=json.dumps(body.answers), urgent=body.urgent)
            d.add(c)
            d.flush()
            alerts = evaluate(d.scalars(select(LoopAlertRule).where(LoopAlertRule.link_id == l_.id,
                                                                     LoopAlertRule.active == True)).all(),  # noqa: E712
                              body.answers, body.urgent)
            for reason, urgent in alerts:
                d.add(LoopAlert(link_id=l_.id, checkin_id=c.id, reason=reason, urgent=urgent))
            d.commit()
            practitioner_email, label, tenant = l_.practitioner_email, l_.athlete_label, l_.tenant_id
        if alerts and practitioner_email:
            urgent = any(u for _, u in alerts)
            send_mail(app, practitioner_email, ("URGENT: " if urgent else "") + "SPPS check-in alert",
                      f"A check-in from {label} raised {len(alerts)} alert(s). Open your SPPS workspace to review: "
                      f"{base_url(request)}/studio")
        return wrap(tenant, {"submitted": True, "due_on": due})

    @app.get("/api/v2/portal/links/{link_id}/guardian-view")
    def guardian_view(link_id: int, acc=Depends(portal_account)):
        with adata(acc) as d:
            l_ = portal_link(d, acc, link_id)
            if l_.guardian_account_id != acc.id:
                raise HTTPException(404, "Not found")
            detail = l_.guardian_detail_by_practitioner and l_.guardian_detail_by_athlete
            if d.get_bind().dialect.name == "postgresql":
                row = d.execute(text("SELECT * FROM spps_identity_dev.v2_loop_participation(:l)"), {"l": l_.id}).one()
                counts = {"assignments_completed": row[0], "assignments_open": row[1], "checkins_submitted": row[2]}
            else:
                counts = {"assignments_completed": len(d.scalars(select(LoopAssignment.id).where(
                              LoopAssignment.link_id == l_.id, LoopAssignment.status == "completed")).all()),
                          "assignments_open": len(d.scalars(select(LoopAssignment.id).where(
                              LoopAssignment.link_id == l_.id, LoopAssignment.status == "assigned")).all()),
                          "checkins_submitted": len(d.scalars(select(LoopCheckin.id).where(
                              LoopCheckin.link_id == l_.id)).all())}
            body = {"participation": counts,
                    "summaries": [{"title": s.title, "body": s.body, "created_at": s.created_at} for s in d.scalars(
                        select(LoopSharedSummary).where(LoopSharedSummary.link_id == l_.id,
                                                        LoopSharedSummary.for_guardian == True))],  # noqa: E712
                    "detail_enabled": detail}
            if detail:
                full = activity(d, l_)
                body["checkins"] = full["checkins"]
                body["assignments"] = full["assignments"]
            return wrap(l_.tenant_id, body)

    @app.get("/api/v2/portal/export")
    def portal_export(acc=Depends(portal_account)):
        with adata(acc) as d:
            links = d.scalars(select(LoopLink).where(LoopLink.athlete_account_id == acc.id)).all()
            out = [{"guardian_of": l_.athlete_label, "practitioner": l_.practitioner_name, "status": l_.status,
                    "consented_at": l_.guardian_consent_at, "ended_at": l_.ended_at}
                   for l_ in d.scalars(select(LoopLink).where(LoopLink.guardian_account_id == acc.id))]
            for l_ in links:
                out.append({"practitioner": l_.practitioner_name, "status": l_.status,
                            "consented_at": l_.athlete_consent_at, "ended_at": l_.ended_at,
                            **{k: v for k, v in _own_activity(d, l_).items()}})
            extra = {}
            for f in EXPORT_EXTRAS:
                extra.update(f(d, acc))
            return {"format": "spps-athlete-export-v1", "account": {"email": acc.email, "name": acc.display_name,
                                                                    "role": acc.role},
                    "links": out, **extra}

    def _own_activity(d, l_):
        return {"submissions": [{"submitted_at": s.submitted_at, "answers": json.loads(s.answers)} for s in d.scalars(
                    select(LoopSubmission).where(LoopSubmission.link_id == l_.id))],
                "checkins": [{"due_on": c.due_on, "answers": json.loads(c.answers), "urgent": c.urgent}
                             for c in d.scalars(select(LoopCheckin).where(LoopCheckin.link_id == l_.id))],
                "summaries": [{"title": s.title, "body": s.body} for s in d.scalars(
                    select(LoopSharedSummary).where(LoopSharedSummary.link_id == l_.id))]}

    from types import SimpleNamespace
    app.state.v2_loop = SimpleNamespace(ident=ident, portal_account=portal_account, wrap=wrap, crisis=crisis,
                                        tenant_id_default=tenant_id_default, create_invite=create_invite,
                                        accept_invite=accept_invite, adata=adata, pdata=pdata, my_link=my_link,
                                        portal_link=portal_link)

    # ------------------------------------------------------------------ timeline for the practitioner
    from .record import TIMELINE_SOURCES

    def loop_timeline(db, user, athlete_id):
        items = []
        with _CURRENT["identity"].data(practitioner=user.id, tenant=user.tenant_id) as d:
            for l_ in d.scalars(select(LoopLink).where(LoopLink.athlete_record_id == athlete_id,
                                                       LoopLink.practitioner_id == user.id)):
                for s in d.scalars(select(LoopSubmission).where(LoopSubmission.link_id == l_.id)):
                    a = d.get(LoopAssignment, s.assignment_id)
                    items.append({"date": s.submitted_at.date().isoformat(), "kind": "assignment", "id": 300000 + s.id,
                                  "title": f"Completed by athlete: {a.title if a else ''}"})
                for c in d.scalars(select(LoopCheckin).where(LoopCheckin.link_id == l_.id)):
                    items.append({"date": c.due_on, "kind": "checkin", "id": 400000 + c.id,
                                  "title": "Check-in" + (" (urgent)" if c.urgent else ""),
                                  "text": ", ".join(f"{k} {v:g}" for k, v in json.loads(c.answers).items())})
                for al in d.scalars(select(LoopAlert).where(LoopAlert.link_id == l_.id)):
                    items.append({"date": al.created_at.date().isoformat(), "kind": "flag", "id": 500000 + al.id,
                                  "title": ("URGENT: " if al.urgent else "") + al.reason})
        return items
    TIMELINE_SOURCES[:] = [f for f in TIMELINE_SOURCES if getattr(f, "__name__", "") != "loop_timeline"]
    TIMELINE_SOURCES.append(loop_timeline)


def _due_reminders_orm(a):
    out = []
    today = date.today()
    for l_ in a.scalars(select(LoopLink).where(LoopLink.status == "active")):
        plan = a.get(LoopCheckinPlan, l_.id)
        if not plan or not plan.active or not l_.athlete_account_id:
            continue
        dues = due_dates(plan, today)
        if dues and not a.scalar(select(LoopCheckin.id).where(LoopCheckin.link_id == l_.id,
                                                              LoopCheckin.due_on == dues[-1])):
            acc = a.get(AthleteAccount, l_.athlete_account_id)
            if acc and acc.status == "active":
                out.append({"email": acc.email, "due_on": dues[-1]})
    return out
