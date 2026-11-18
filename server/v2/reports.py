"""Phase 5: practitioner-authored reports for the athlete, guardian, coach and medical/MDT team.

* Templates list fields; each field is tagged with the audiences allowed to see it. A field with no audience is
  clinical-only. ``render`` is the single redaction point: it builds the report for ONE audience from the fields
  tagged with that audience and nothing else. Every output (JSON, PDF, shared link) is produced from ``render``.
* Narrative is typed by the practitioner. Scores and charts are pulled from the athlete's instrument data. Nothing
  is generated.
* The practitioner reviews and signs; signing freezes the rendered snapshot (encrypted) and its hash. Only a
  signed report can be downloaded as PDF or shared.
* Sharing link (flag SPPS_REPORT_LINKS_ENABLED): expiring, revocable, single audience, requires a recorded consent
  for that audience (from the guardian when the athlete is under 18). An expired, revoked or consent-withdrawn link
  returns nothing (404, the same answer as an unknown link). Every view is audited.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import secrets
from datetime import timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from ..models import Athlete, now
from . import fieldcrypt, flags, guards
from .assessments import Instrument, InstrumentAdministration, InstrumentScore, subscales_for
from .context import Ctx, is_pg
from .models import (AUDIENCES, AthleteProfile, ReportConsent, ReportLink, ReportLinkView, ReportTemplate,
                     SharedReport)
from .record import is_minor

KINDS = ("narrative", "scores", "chart")
AUDIENCE_LABEL = {"athlete": "Athlete", "guardian": "Parent / guardian", "coach": "Coach",
                  "medical": "Medical / multidisciplinary team"}


def token_hash(token: str) -> str:
    return hashlib.sha256(("spps-report-link:" + token).encode()).hexdigest()


def _aware(v):
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class FieldIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    kind: str = "narrative"
    audiences: list[str] = Field(default_factory=list)  # empty = clinical-only
    instrument_id: int | None = None


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    fields: list[FieldIn] = Field(min_length=1, max_length=30)


class ReportIn(BaseModel):
    template_id: int
    audience: str
    title: str = Field(min_length=3, max_length=140)


class ValuesIn(BaseModel):
    values: dict[str, str]
    expected_revision: int = Field(ge=1)


class SignIn(BaseModel):
    expected_revision: int = Field(ge=1)


class ConsentIn(BaseModel):
    audience: str
    given_by: str
    method: str = "written"
    note: str = Field(default="", max_length=240)


class LinkIn(BaseModel):
    expires_in_days: int = Field(default=14, ge=1, le=30)
    recipient_label: str = Field(default="", max_length=120)


# ------------------------------------------------------------------ the single redaction point
def score_block(db, user, athlete_id, instrument_id) -> dict:
    inst = db.get(Instrument, instrument_id) if instrument_id else None
    if not inst or (inst.practitioner_id not in (None, user.id)) or inst.tenant_id != user.tenant_id:
        return {"instrument": None, "series": [], "dates": []}
    adms = db.scalars(select(InstrumentAdministration).where(
        InstrumentAdministration.athlete_id == athlete_id, InstrumentAdministration.practitioner_id == user.id,
        InstrumentAdministration.instrument_id == inst.id).order_by(InstrumentAdministration.administered_on)).all()
    series = []
    for sub in subscales_for(db, inst):
        pts, missing = [], []
        for a in adms:
            s = db.scalar(select(InstrumentScore).where(InstrumentScore.administration_id == a.id,
                                                        InstrumentScore.subscale_key == sub["key"]))
            (pts.append({"date": a.administered_on, "score": s.score}) if s is not None and s.score is not None
             else missing.append(a.administered_on))
        series.append({"label": sub["label"], "min": sub["min"], "max": sub["max"], "points": pts, "missing": missing})
    return {"instrument": inst.short_name or inst.name, "series": series, "dates": [a.administered_on for a in adms]}


def render(db, user, report: SharedReport, template: ReportTemplate, audience: str) -> dict:
    """Build the report for one audience. Fields not tagged with that audience are not read into the output."""
    if audience not in AUDIENCES:
        raise HTTPException(422, "Unknown audience")
    values = json.loads(report.values or "{}")
    sections = []
    for f in json.loads(template.fields):
        if audience not in (f.get("audiences") or []):
            continue  # clinical-only or for another audience: never copied into the output
        sec = {"key": f["key"], "label": f["label"], "kind": f["kind"]}
        if f["kind"] == "narrative":
            sec["text"] = values.get(f["key"], "")
        else:
            sec["data"] = score_block(db, user, report.athlete_id, f.get("instrument_id"))
        sections.append(sec)
    athlete = db.get(Athlete, report.athlete_id)
    return {"title": report.title, "audience": audience, "audience_label": AUDIENCE_LABEL[audience],
            "athlete": athlete.display_name if athlete else "", "sections": sections,
            "signed_at": report.signed_at.isoformat() if report.signed_at else None,
            "notice": "Confidential. Prepared and signed by the practitioner for the named audience only. "
                      "Not a crisis service."}


def trend_svg(series: dict, dates: list[str]) -> str:
    """Server-side twin of the browser chart: dated points, missing marked, no line across a gap."""
    if not dates:
        return "<p>No administrations.</p>"
    from datetime import date as d_
    W, H, P = 560, 160, 34
    t = lambda s: d_.fromisoformat(s).toordinal()  # noqa: E731
    t0, t1 = t(dates[0]), t(dates[-1])
    x = lambda s: W / 2 if len(dates) == 1 else P + (W - 2 * P) * (t(s) - t0) / ((t1 - t0) or 1)  # noqa: E731
    y = lambda v: H - P - (H - 2 * P) * ((v - series["min"]) / ((series["max"] - series["min"]) or 1))  # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {H}" style="width:100%;max-width:{W}px" role="img">',
           f'<line x1="{P}" y1="{H - P}" x2="{W - P}" y2="{H - P}" stroke="#ccd"/>']
    pts = series["points"]
    for a, b in zip(pts, pts[1:]):
        if not any(a["date"] < m < b["date"] for m in series["missing"]):
            out.append(f'<line x1="{x(a["date"]):.1f}" y1="{y(a["score"]):.1f}" x2="{x(b["date"]):.1f}" '
                       f'y2="{y(b["score"]):.1f}" stroke="#1f8a8a" stroke-width="2"/>')
    for p in pts:
        out.append(f'<circle cx="{x(p["date"]):.1f}" cy="{y(p["score"]):.1f}" r="5" fill="#1f8a8a"/>'
                   f'<text x="{x(p["date"]):.1f}" y="{y(p["score"]) - 9:.1f}" font-size="10" text-anchor="middle">'
                   f'{html.escape(str(p["score"]))}</text>')
    for m in series["missing"]:
        out.append(f'<circle cx="{x(m):.1f}" cy="{H - P}" r="5" fill="none" stroke="#c0396b" stroke-width="2"/>'
                   f'<text x="{x(m):.1f}" y="{H - P + 24}" font-size="9" text-anchor="middle">missing</text>')
    for dd in dates:
        out.append(f'<text x="{x(dd):.1f}" y="{H - P + 13}" font-size="9" text-anchor="middle">{html.escape(dd)}</text>')
    return "".join(out) + "</svg>"


def snapshot_html(snap: dict) -> str:
    e = html.escape
    parts = [f"<span class=\"badge gold\">{e(snap['audience_label'])}</span><h1>{e(snap['title'])}</h1>",
             f"<p class=\"small muted\">{e(snap['athlete'])} · signed {e((snap['signed_at'] or '')[:10])}</p>"]
    for s in snap["sections"]:
        parts.append(f"<h2>{e(s['label'])}</h2>")
        if s["kind"] == "narrative":
            parts.append("".join(f"<p>{e(line)}</p>" for line in (s.get("text") or "").splitlines() if line.strip()))
        else:
            data = s["data"]
            for ser in data["series"]:
                parts.append(f"<h3>{e(data['instrument'] or '')} · {e(ser['label'])}</h3>")
                if s["kind"] == "chart":
                    parts.append(trend_svg(ser, data["dates"]))
                rows = "".join(f"<tr><td>{e(p['date'])}</td><td>{e(str(p['score']))}</td></tr>" for p in ser["points"])
                rows += "".join(f"<tr><td>{e(m)}</td><td>missing</td></tr>" for m in ser["missing"])
                parts.append(f"<table class=\"data-table\"><tr><th>Date</th><th>Score</th></tr>{rows}</table>")
    parts.append(f"<p class=\"foot-note\">{e(snap['notice'])}</p>")
    return "".join(parts)


def snapshot_pdf(snap: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
    e = html.escape
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=48, leftMargin=48, topMargin=48, bottomMargin=48,
                            title=snap["title"])
    st = getSampleStyleSheet()
    el = [Paragraph(e(f"SPPS · {snap['audience_label']} report"), st["Heading3"]), Paragraph(e(snap["title"]), st["Title"]),
          Paragraph(e(f"{snap['athlete']} · signed {(snap['signed_at'] or '')[:10]}"), st["Normal"]), Spacer(1, 12)]
    for s in snap["sections"]:
        el.append(Paragraph(e(s["label"]), st["Heading2"]))
        if s["kind"] == "narrative":
            for line in (s.get("text") or "").splitlines():
                el.append(Paragraph(e(line or " "), st["BodyText"]))
        else:
            for ser in s["data"]["series"]:
                el.append(Paragraph(e(f"{s['data']['instrument'] or ''} · {ser['label']}"), st["Heading4"]))
                rows = [["Date", "Score"]] + [[p["date"], str(p["score"])] for p in ser["points"]] + \
                       [[m, "missing"] for m in ser["missing"]]
                el.append(Table(rows))
        el.append(Spacer(1, 8))
    el.append(Paragraph(e(snap["notice"]), st["Italic"]))
    doc.build(el)
    return buf.getvalue()


def practitioner_in_good_standing(db, practitioner_id: int) -> bool:
    """A deactivated, closed, rejected or under-review practitioner's links stop opening (0041 on PostgreSQL)."""
    from ..models import Practitioner, PractitionerProfile
    p = db.get(Practitioner, practitioner_id)
    if not p or not p.active:
        return False
    prof = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == practitioner_id))
    return prof is None or prof.status == "approved"


def active_consent(db, report: SharedReport) -> ReportConsent | None:
    prof = db.get(AthleteProfile, report.athlete_id)
    needed = "guardian" if prof and is_minor(prof.age_group) else None
    q = select(ReportConsent).where(ReportConsent.athlete_id == report.athlete_id,
                                    ReportConsent.practitioner_id == report.practitioner_id,
                                    ReportConsent.audience == report.audience, ReportConsent.withdrawn_at.is_(None))
    if needed:
        q = q.where(ReportConsent.given_by == "guardian")
    return db.scalars(q.order_by(ReportConsent.id.desc())).first()


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, athlete_for = ctx.db_session, ctx.get_current, ctx.athlete_for

    # ------------------------------------------------------------------ templates
    @app.get("/api/v2/report-templates")
    def list_templates(user=Depends(get_current), db=Depends(db_session)):
        return [{"id": t.id, "name": t.name, "fields": json.loads(t.fields)} for t in db.scalars(
            select(ReportTemplate).where(ReportTemplate.practitioner_id == user.id, ReportTemplate.active == True))]  # noqa

    @app.post("/api/v2/report-templates", status_code=201)
    def new_template(body: TemplateIn, user=Depends(get_current), db=Depends(db_session)):
        keys = [f.key for f in body.fields]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, "Field keys must be unique")
        for f in body.fields:
            if f.kind not in KINDS or set(f.audiences) - set(AUDIENCES):
                raise HTTPException(422, f"Field '{f.label}': unknown kind or audience")
            if f.kind != "narrative":
                if not f.instrument_id:
                    raise HTTPException(422, f"Field '{f.label}' needs an instrument for its scores")
                from .assessments import visible_instrument
                visible_instrument(db, user, f.instrument_id)
        t = ReportTemplate(tenant_id=user.tenant_id, practitioner_id=user.id, name=body.name.strip(),
                           fields=json.dumps([f.model_dump() for f in body.fields]))
        db.add(t)
        db.commit()
        return {"id": t.id}

    def own_template(db, user, tid) -> ReportTemplate:
        t = db.scalar(select(ReportTemplate).where(ReportTemplate.id == tid, ReportTemplate.practitioner_id == user.id))
        if not t:
            raise HTTPException(404, "Template not found")
        return t

    def own_report(db, user, rid) -> SharedReport:
        r = db.scalar(select(SharedReport).where(SharedReport.id == rid, SharedReport.practitioner_id == user.id,
                                                 SharedReport.tenant_id == user.tenant_id))
        if not r:
            raise HTTPException(404, "Report not found")
        athlete_for(db, user, r.athlete_id)
        return r

    def report_json(db, user, r):
        t = db.get(ReportTemplate, r.template_id)
        fields = json.loads(t.fields)
        return {"id": r.id, "athlete_id": r.athlete_id, "title": r.title, "audience": r.audience,
                "status": r.status, "revision": r.revision, "signed_at": r.signed_at,
                "values": json.loads(r.values or "{}"),
                "fields": [{**f, "included": r.audience in (f.get("audiences") or []),
                            "clinical_only": not f.get("audiences")} for f in fields]}

    # ------------------------------------------------------------------ reports
    @app.get("/api/v2/athletes/{athlete_id}/v2-reports")
    def athlete_reports(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        return [{"id": r.id, "title": r.title, "audience": r.audience, "status": r.status, "signed_at": r.signed_at}
                for r in db.scalars(select(SharedReport).where(SharedReport.athlete_id == athlete_id,
                                                               SharedReport.practitioner_id == user.id)
                                    .order_by(SharedReport.id.desc()))]

    @app.post("/api/v2/athletes/{athlete_id}/v2-reports", status_code=201)
    def new_report(athlete_id: int, body: ReportIn, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        own_template(db, user, body.template_id)
        if body.audience not in AUDIENCES:
            raise HTTPException(422, "Choose athlete, guardian, coach or medical")
        r = SharedReport(tenant_id=user.tenant_id, practitioner_id=user.id, athlete_id=athlete_id,
                         template_id=body.template_id, title=body.title.strip(), audience=body.audience, values="{}")
        db.add(r)
        db.commit()
        return report_json(db, user, r)

    @app.get("/api/v2/v2-reports/{rid}")
    def get_report(rid: int, user=Depends(get_current), db=Depends(db_session)):
        return report_json(db, user, own_report(db, user, rid))

    @app.put("/api/v2/v2-reports/{rid}")
    def put_values(rid: int, body: ValuesIn, user=Depends(get_current), db=Depends(db_session)):
        r = own_report(db, user, rid)
        if r.status != "draft" or r.revision != body.expected_revision:
            raise HTTPException(409, "The report is signed or was changed elsewhere")
        fields = {f["key"]: f for f in json.loads(db.get(ReportTemplate, r.template_id).fields)}
        for k, v in body.values.items():
            if k not in fields or fields[k]["kind"] != "narrative" or len(v) > 12000:
                raise HTTPException(422, f"'{k}' is not a narrative field of this report")
        merged = {**json.loads(r.values or "{}"), **body.values}
        r.values, r.revision = json.dumps(merged), r.revision + 1
        db.commit()
        return report_json(db, user, r)

    @app.get("/api/v2/v2-reports/{rid}/preview")
    def preview(rid: int, user=Depends(get_current), db=Depends(db_session)):
        r = own_report(db, user, rid)
        return render(db, user, r, db.get(ReportTemplate, r.template_id), r.audience)

    @app.post("/api/v2/v2-reports/{rid}/sign")
    def sign(rid: int, body: SignIn, user=Depends(get_current), db=Depends(db_session)):
        r = own_report(db, user, rid)
        if r.status != "draft" or r.revision != body.expected_revision:
            raise HTTPException(409, "The report is already signed or was changed elsewhere")
        r.signed_at = now()
        snap = render(db, user, r, db.get(ReportTemplate, r.template_id), r.audience)
        raw = json.dumps(snap, sort_keys=True)
        r.snapshot, r.content_sha256, r.status = raw, hashlib.sha256(raw.encode()).hexdigest(), "signed"
        ctx.event(db, user, "report.signed", "shared_report", r.id)
        db.commit()
        return report_json(db, user, r)

    def signed_snapshot(r) -> dict:
        if r.status != "signed" or not r.snapshot:
            raise HTTPException(409, "Sign the report before downloading or sharing it")
        return json.loads(r.snapshot)

    @app.get("/api/v2/v2-reports/{rid}/rendered")
    def rendered(rid: int, user=Depends(get_current), db=Depends(db_session)):
        return signed_snapshot(own_report(db, user, rid))

    @app.get("/api/v2/v2-reports/{rid}/pdf")
    def pdf(rid: int, user=Depends(get_current), db=Depends(db_session)):
        r = own_report(db, user, rid)
        data = snapshot_pdf(signed_snapshot(r))
        ctx.event(db, user, "report.pdf_downloaded", "shared_report", r.id)
        db.commit()
        return StreamingResponse(iter([data]), media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="spps-{r.audience}-report-{r.id}.pdf"',
            "Cache-Control": "no-store"})

    # ------------------------------------------------------------------ consent per audience
    @app.get("/api/v2/athletes/{athlete_id}/report-consents")
    def consents(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        return [{"id": c.id, "audience": c.audience, "given_by": c.given_by, "method": c.method, "note": c.note,
                 "recorded_at": c.recorded_at, "withdrawn_at": c.withdrawn_at}
                for c in db.scalars(select(ReportConsent).where(ReportConsent.athlete_id == athlete_id,
                                                                ReportConsent.practitioner_id == user.id)
                                    .order_by(ReportConsent.id.desc()))]

    @app.post("/api/v2/athletes/{athlete_id}/report-consents", status_code=201)
    def record_consent(athlete_id: int, body: ConsentIn, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        if body.audience not in AUDIENCES or body.given_by not in ("athlete", "guardian") or \
                body.method not in ("written", "verbal"):
            raise HTTPException(422, "Choose an audience, who gave consent and how")
        prof = db.get(AthleteProfile, athlete_id)
        if prof and is_minor(prof.age_group) and body.given_by != "guardian":
            raise HTTPException(422, "For an under-18 athlete, consent to share is recorded from the guardian")
        c = ReportConsent(tenant_id=user.tenant_id, practitioner_id=user.id, athlete_id=athlete_id,
                          audience=body.audience, given_by=body.given_by, method=body.method, note=body.note)
        db.add(c)
        ctx.event(db, user, "report.consent_recorded", "athlete", athlete_id)
        db.commit()
        return {"id": c.id}

    @app.post("/api/v2/report-consents/{cid}/withdraw")
    def withdraw_consent(cid: int, user=Depends(get_current), db=Depends(db_session)):
        c = db.scalar(select(ReportConsent).where(ReportConsent.id == cid, ReportConsent.practitioner_id == user.id))
        if not c:
            raise HTTPException(404, "Consent not found")
        if not c.withdrawn_at:
            c.withdrawn_at = now()
            ctx.event(db, user, "report.consent_withdrawn", "athlete", c.athlete_id)
            db.commit()
        return {"withdrawn_at": c.withdrawn_at}

    # ------------------------------------------------------------------ expiring, revocable links
    def links_on():
        if not flags.report_links_enabled():
            raise HTTPException(404, "Not found")

    @app.post("/api/v2/v2-reports/{rid}/links", status_code=201)
    def create_link(rid: int, body: LinkIn, request: Request, user=Depends(get_current), db=Depends(db_session)):
        links_on()
        r = own_report(db, user, rid)
        signed_snapshot(r)
        consent = active_consent(db, r)
        if not consent:
            raise HTTPException(409, f"Record the {'guardian' if (db.get(AthleteProfile, r.athlete_id) and is_minor(db.get(AthleteProfile, r.athlete_id).age_group)) else 'athlete'}'s "
                                     f"consent to share with the {AUDIENCE_LABEL[r.audience].lower()} first")
        token = secrets.token_urlsafe(32)
        link = ReportLink(tenant_id=user.tenant_id, practitioner_id=user.id, report_id=r.id, consent_id=consent.id,
                          token_hash=token_hash(token), recipient_label=body.recipient_label.strip(),
                          expires_at=now() + timedelta(days=body.expires_in_days))
        db.add(link)
        ctx.event(db, user, "report.link_created", "shared_report", r.id)
        db.commit()
        base = __import__("os").getenv("SPPS_PUBLIC_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        return {"id": link.id, "url": f"{base}/r/{token}", "expires_at": link.expires_at,
                "notice": "Copy the link now: it is shown only once. Anyone holding it can open the report until "
                          "it expires or you revoke it."}

    @app.get("/api/v2/v2-reports/{rid}/links")
    def list_links(rid: int, user=Depends(get_current), db=Depends(db_session)):
        r = own_report(db, user, rid)
        out = []
        for l_ in db.scalars(select(ReportLink).where(ReportLink.report_id == r.id).order_by(ReportLink.id.desc())):
            views = db.scalars(select(ReportLinkView).where(ReportLinkView.link_id == l_.id)).all()
            out.append({"id": l_.id, "recipient_label": l_.recipient_label, "expires_at": l_.expires_at,
                        "revoked_at": l_.revoked_at, "views": [{"at": v.viewed_at, "outcome": v.outcome} for v in views]})
        return out

    @app.post("/api/v2/report-links/{lid}/revoke")
    def revoke_link(lid: int, user=Depends(get_current), db=Depends(db_session)):
        l_ = db.scalar(select(ReportLink).where(ReportLink.id == lid, ReportLink.practitioner_id == user.id))
        if not l_:
            raise HTTPException(404, "Link not found")
        if not l_.revoked_at:
            l_.revoked_at = now()
            ctx.event(db, user, "report.link_revoked", "shared_report", l_.report_id)
            db.commit()
        return {"revoked_at": l_.revoked_at}

    def revoke_report_links_on_closure(db, user):
        for l_ in db.scalars(select(ReportLink).where(ReportLink.practitioner_id == user.id,
                                                      ReportLink.revoked_at.is_(None))):
            l_.revoked_at = now()
        db.commit()
    from .trust import add_closure_hook
    add_closure_hook(app, revoke_report_links_on_closure)

    def open_link(db, token: str) -> dict | None:
        """Anonymous. Returns the signed snapshot or None. Every attempt on a known link is audited."""
        th = token_hash(token)
        if is_pg(db):
            raw = db.execute(text("SELECT spps_dev.v2_open_report_link(:h)"), {"h": th}).scalar()
            db.commit()
            return json.loads(fieldcrypt.decrypt(raw, "shared_reports.snapshot")) if raw else None
        l_ = db.scalar(select(ReportLink).where(ReportLink.token_hash == th))
        if not l_:
            return None
        r = db.get(SharedReport, l_.report_id)
        c = db.get(ReportConsent, l_.consent_id)
        outcome = ("revoked" if l_.revoked_at or not practitioner_in_good_standing(db, l_.practitioner_id)
                   else "expired" if _aware(l_.expires_at) < now()
                   else "withdrawn" if (not c or c.withdrawn_at) else "shown")
        db.add(ReportLinkView(link_id=l_.id, tenant_id=l_.tenant_id, practitioner_id=l_.practitioner_id, outcome=outcome))
        db.commit()
        return json.loads(r.snapshot) if outcome == "shown" and r and r.snapshot else None

    @app.get("/api/v2/report-links/{token}")
    def public_link_json(token: str, request: Request, db=Depends(db_session)):
        links_on()
        guards.limit_ip(request, "report-link")
        snap = open_link(db, token) if 20 <= len(token) <= 80 else None
        if not snap:
            raise HTTPException(404, "Not found")
        return snap

    @app.get("/r/{token}")
    def public_link_page(token: str, request: Request, db=Depends(db_session)):
        links_on()
        guards.limit_ip(request, "report-link")
        snap = open_link(db, token) if 20 <= len(token) <= 80 else None
        if not snap:
            return HTMLResponse(_page("<h1>Not available</h1><p>This link has expired, was withdrawn, or does not "
                                      "exist. Ask the practitioner for a new one.</p>"), status_code=404,
                                headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})
        return HTMLResponse(_page(snapshot_html(snap)), headers={"Cache-Control": "no-store",
                                                                 "X-Robots-Tag": "noindex, nofollow"})


def _page(body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width,initial-scale=1\"><title>SPPS report</title><link rel=\"stylesheet\" "
            "href=\"/assets/styles.css\"></head><body data-theme=\"original\"><header class=\"site-header\"><div "
            "class=\"shell\"><span class=\"brand\"><span class=\"brand-mark\">SPPS</span><span><span class=\"brand-name\">"
            "Sport Psychology<br>Practitioner Suite</span></span></span></div></header><main class=\"legal\" "
            f"style=\"padding-top:48px\">{body}</main></body></html>")


def install_hooks():
    from .record import TIMELINE_SOURCES
    from .trust import EXPORTERS, PURGE_HOOKS, row_dict

    def timeline(db, user, athlete_id):
        return [{"date": r.created_at.date().isoformat(), "kind": "report", "id": 200000 + r.id,
                 "title": f"{r.title} ({AUDIENCE_LABEL.get(r.audience, r.audience)})", "state": r.status}
                for r in db.scalars(select(SharedReport).where(SharedReport.athlete_id == athlete_id,
                                                               SharedReport.practitioner_id == user.id))]
    TIMELINE_SOURCES.append(timeline)
    for name, model in (("report_templates", ReportTemplate), ("shared_reports", SharedReport),
                        ("report_consents", ReportConsent), ("report_links", ReportLink)):
        EXPORTERS[name] = (lambda m: lambda db, user: [row_dict(x, skip=("token_hash",)) for x in db.scalars(
            select(m).where(m.practitioner_id == user.id))])(model)

    def purge(db, ids):
        rids = [r.id for r in db.scalars(select(SharedReport).where(SharedReport.athlete_id.in_(ids)))]
        lids = [l_.id for l_ in db.scalars(select(ReportLink).where(ReportLink.report_id.in_(rids or [0])))]
        db.query(ReportLinkView).filter(ReportLinkView.link_id.in_(lids or [0])).delete(synchronize_session=False)
        db.query(ReportLink).filter(ReportLink.report_id.in_(rids or [0])).delete(synchronize_session=False)
        db.query(SharedReport).filter(SharedReport.athlete_id.in_(ids)).delete(synchronize_session=False)
        db.query(ReportConsent).filter(ReportConsent.athlete_id.in_(ids)).delete(synchronize_session=False)
    PURGE_HOOKS.insert(0, purge)


install_hooks()
