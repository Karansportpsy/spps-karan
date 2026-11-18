"""Phase 3: the practitioner record (caseloads of 1-25 athletes, replacing paper and spreadsheets).

* Athlete profile: sport, level, age group, guardian contact (required for under-18s), consent status, referral.
* Consultation notes: practitioner templates, server autosave of drafts (plus an encrypted offline draft in the
  browser), sign-and-lock (hash of the signed text), amend-with-history.
* Import: CSV or Excel upload -> map columns on screen -> preview with every row checked -> commit all or nothing.
* Case formulation: the 5Ps scaffold, practitioner-written only, versioned.
* Timeline: notes, amendments, observations, assessments, interventions, formulations, reports and flags by date.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import re
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..models import (AssessmentAdministration, Assignment, Athlete, Case, Factor, Intervention, Note, Observation,
                      Report, now)
from . import fieldcrypt, guards
from .context import Ctx
from .models import (AGE_GROUPS, CONSENT_STATES, LEVELS, MINOR_AGE_GROUPS, AthleteImport, AthleteProfile,
                     CaseFormulation, NoteAmendment, NoteDraft, NoteSignature, NoteTemplate)

FIVE_PS = ("presenting", "predisposing", "precipitating", "perpetuating", "protective")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_ROWS = 1000
IMPORT_TARGETS = ("code", "display_name", "sport", "athlete_group", "level", "age_group", "guardian_name",
                  "guardian_email", "guardian_phone", "consent_recorded", "referral_source")
REQUIRED_TARGETS = ("code", "display_name", "sport")
# Extra timeline sources registered by later phases: callable(db, user, athlete_id) -> list[dict]
TIMELINE_SOURCES: list = []


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_minor(age_group: str) -> bool:
    return age_group in MINOR_AGE_GROUPS


class ProfileIn(BaseModel):
    level: str = "not specified"
    age_group: str = "not specified"
    guardian_name: str = Field(default="", max_length=120)
    guardian_email: str = Field(default="", max_length=250)
    guardian_phone: str = Field(default="", max_length=24)
    consent_status: str = "not_recorded"
    referral_source: str = Field(default="", max_length=120)
    referral_notes: str = Field(default="", max_length=4000)


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=2, max_length=8000)


class DraftIn(BaseModel):
    case_id: int
    note_date: date
    content: str = Field(max_length=16000)
    expected_revision: int = Field(default=0, ge=0)


class SignIn(BaseModel):
    expected_revision: int = Field(ge=1)


class AmendIn(BaseModel):
    content: str = Field(min_length=2, max_length=16000)
    reason: str = Field(min_length=3, max_length=240)


class FormulationIn(BaseModel):
    presenting: str = Field(default="", max_length=6000)
    predisposing: str = Field(default="", max_length=6000)
    precipitating: str = Field(default="", max_length=6000)
    perpetuating: str = Field(default="", max_length=6000)
    protective: str = Field(default="", max_length=6000)
    expected_version: int = Field(default=0, ge=0)


class ImportUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=4, max_length=2_800_000)


class ScoreMap(BaseModel):
    column: str
    factor_id: int
    date_column: str


class ImportMappingIn(BaseModel):
    mapping: dict[str, str]
    scores: list[ScoreMap] = Field(default_factory=list, max_length=20)


# ------------------------------------------------------------------ import parsing and validation
def parse_upload(filename: str, raw: bytes) -> tuple[list[str], list[list[str]]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(422, "The Excel file could not be read") from None
        ws = wb.worksheets[0]
        rows = [["" if c is None else (c.isoformat()[:10] if isinstance(c, (datetime, date)) else str(c)).strip()
                 for c in r] for r in ws.iter_rows(values_only=True)]
    elif name.endswith((".csv", ".txt")):
        try:
            text_ = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_ = raw.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text_[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text_), dialect)]
    else:
        raise HTTPException(422, "Upload a .csv or .xlsx file")
    rows = [r for r in rows if any(cell for cell in r)]
    if not rows:
        raise HTTPException(422, "The file is empty")
    header = [h or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    if len(set(header)) != len(header):
        raise HTTPException(422, "Two columns have the same heading; rename one and upload again")
    body = [r + [""] * (len(header) - len(r)) for r in rows[1:]]
    if len(body) > MAX_IMPORT_ROWS:
        raise HTTPException(422, f"At most {MAX_IMPORT_ROWS} rows can be imported at once")
    return header, body


def suggest_mapping(header: list[str]) -> dict[str, str]:
    aliases = {"code": ("code", "id", "athlete id", "athlete code"), "display_name": ("name", "athlete", "athlete name",
               "display name", "full name"), "sport": ("sport",), "athlete_group": ("group", "squad", "team"),
               "level": ("level",), "age_group": ("age group", "age"), "guardian_name": ("guardian", "guardian name",
               "parent", "parent name"), "guardian_email": ("guardian email", "parent email"),
               "guardian_phone": ("guardian phone", "parent phone"), "consent_recorded": ("consent",),
               "referral_source": ("referral", "referred by", "referral source")}
    out = {}
    low = {h.strip().lower(): h for h in header}
    for target, names in aliases.items():
        for n in names:
            if n in low:
                out[target] = low[n]
                break
    return out


def _norm_level(v: str) -> str | None:
    v = (v or "").strip().lower() or "not specified"
    return v if v in LEVELS else None


def _norm_age(v: str) -> str | None:
    v = (v or "").strip().lower().replace("u12", "under-12").replace("under 12", "under-12").replace("–", "-")
    v = v or "not specified"
    return v if v in AGE_GROUPS else None


def _yes(v: str) -> bool | None:
    v = (v or "").strip().lower()
    if v in ("yes", "y", "true", "1", "recorded"):
        return True
    if v in ("", "no", "n", "false", "0", "not recorded"):
        return False
    return None


def _date(v: str) -> str | None:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def validate_rows(db, user, header, body, mapping: dict[str, str], scores: list[ScoreMap]):
    """Every row checked; returns (clean rows, errors). Nothing is written here."""
    unknown_targets = set(mapping) - set(IMPORT_TARGETS)
    if unknown_targets:
        raise HTTPException(422, "Unknown field: " + ", ".join(sorted(unknown_targets)))
    missing = [t for t in REQUIRED_TARGETS if not mapping.get(t)]
    if missing:
        raise HTTPException(422, "Choose a column for: " + ", ".join(missing))
    idx = {h: i for i, h in enumerate(header)}
    for col in list(mapping.values()) + [s.column for s in scores] + [s.date_column for s in scores]:
        if col not in idx:
            raise HTTPException(422, f"Column '{col}' is not in the file")
    factors = {}
    for s in scores:
        f = db.scalar(select(Factor).where(Factor.id == s.factor_id, Factor.tenant_id == user.tenant_id,
                                           Factor.active == True))  # noqa: E712
        if not f or (f.owner_practitioner_id not in (None, user.id)):
            raise HTTPException(404, "Score measure not found")
        factors[s.factor_id] = f
    existing = set(db.scalars(select(Athlete.code).where(Athlete.owner_practitioner_id == user.id,
                                                         Athlete.tenant_id == user.tenant_id)))
    seen: dict[str, int] = {}
    clean, errors = [], []
    for n, row in enumerate(body, start=2):  # spreadsheet row numbers (header is row 1)
        get = lambda t: row[idx[mapping[t]]].strip() if mapping.get(t) else ""  # noqa: E731
        rec, errs = {}, []
        code = get("code")
        if not CODE_RE.fullmatch(code):
            errs.append(("code", "Use 2-32 letters, digits, - or _"))
        elif code in existing:
            errs.append(("code", "An athlete with this code already exists in your caseload"))
        elif code in seen:
            errs.append(("code", f"Same code as row {seen[code]}"))
        else:
            seen[code] = n
        name, sport = get("display_name"), get("sport")
        if not 2 <= len(name) <= 120:
            errs.append(("display_name", "Name must be 2-120 characters"))
        if not 2 <= len(sport) <= 80:
            errs.append(("sport", "Sport must be 2-80 characters"))
        level, age = _norm_level(get("level")), _norm_age(get("age_group"))
        if level is None:
            errs.append(("level", "Unknown level; use one of: " + ", ".join(LEVELS)))
        if age is None:
            errs.append(("age_group", "Unknown age group; use one of: " + ", ".join(AGE_GROUPS)))
        g_name, g_email, g_phone = get("guardian_name"), get("guardian_email"), get("guardian_phone")
        if g_email and not EMAIL_RE.fullmatch(g_email):
            errs.append(("guardian_email", "Not a valid e-mail address"))
        if age and is_minor(age) and not (g_name and (g_email or g_phone)):
            errs.append(("guardian_name", "Under-18: guardian name and an e-mail or phone are required"))
        consent = _yes(get("consent_recorded"))
        if consent is None:
            errs.append(("consent_recorded", "Use yes or no"))
        group = get("athlete_group") or "not specified"
        if len(group) > 40:
            errs.append(("athlete_group", "At most 40 characters"))
        score_rows = []
        for s in scores:
            raw_v, raw_d = row[idx[s.column]].strip(), row[idx[s.date_column]].strip()
            if not raw_v:
                continue  # missing stays missing
            f = factors[s.factor_id]
            try:
                value = float(raw_v)
            except ValueError:
                errs.append((s.column, "Score must be a number"))
                continue
            if not f.low <= value <= f.high:
                errs.append((s.column, f"Score must be between {f.low:g} and {f.high:g}"))
            d = _date(raw_d)
            if not d:
                errs.append((s.date_column, "Date must be YYYY-MM-DD or DD/MM/YYYY"))
            score_rows.append((f.id, value, d))
        rec.update(code=code, display_name=name, sport=sport, athlete_group=group, level=level, age_group=age,
                   guardian_name=g_name[:120], guardian_email=g_email[:250], guardian_phone=g_phone[:24],
                   consent=bool(consent), referral_source=get("referral_source")[:120], scores=score_rows)
        if errs:
            errors.extend({"row": n, "field": f, "message": m} for f, m in errs)
        else:
            clean.append(rec)
    return clean, errors


def offline_key(user, kid: str | None = None) -> tuple[str, bytes]:
    """Per-practitioner key for the browser's encrypted offline draft, derived from a field key. The browser keeps
    it in memory only; a stored offline draft can therefore be read only after signing in again."""
    current, keys = fieldcrypt._keys()
    kid = kid or current
    if not kid or kid not in keys:
        if fieldcrypt.configured():
            raise HTTPException(404, "Key not available")
        kid, secret = "local", guards._LOCAL_SECRET
    else:
        secret = keys[kid]
    return kid, hmac.new(secret, f"spps-offline-draft:{user.tenant_id}:{user.id}".encode(), hashlib.sha256).digest()


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, athlete_for, case_for = ctx.db_session, ctx.get_current, ctx.athlete_for, ctx.case_for

    def own_note(db, user, note_id) -> Note:
        n = db.scalar(select(Note).where(Note.id == note_id, Note.tenant_id == user.tenant_id, Note.author_id == user.id))
        if not n:
            raise HTTPException(404, "Note not found")
        case_for(db, user, n.case_id)  # also logs the access
        return n

    # ------------------------------------------------------------------ athlete profile
    def profile_for(db, user, athlete_id) -> AthleteProfile:
        a = athlete_for(db, user, athlete_id)
        p = db.get(AthleteProfile, a.id)
        if not p:
            p = AthleteProfile(athlete_id=a.id, tenant_id=user.tenant_id, practitioner_id=user.id,
                               consent_status="recorded" if a.service_consent_recorded_at else "not_recorded")
        return p

    def profile_json(a, p):
        return {"athlete_id": a.id, "code": a.code, "display_name": a.display_name, "sport": a.sport,
                "athlete_group": a.athlete_group, "level": p.level, "age_group": p.age_group,
                "is_minor": is_minor(p.age_group), "guardian_name": p.guardian_name,
                "guardian_email": p.guardian_email, "guardian_phone": p.guardian_phone,
                "consent_status": p.consent_status, "consent_changed_at": p.consent_changed_at,
                "referral_source": p.referral_source, "referral_notes": p.referral_notes}

    @app.get("/api/v2/athletes/{athlete_id}/profile")
    def get_profile(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        a = athlete_for(db, user, athlete_id)
        return profile_json(a, profile_for(db, user, athlete_id))

    @app.put("/api/v2/athletes/{athlete_id}/profile")
    def put_profile(athlete_id: int, body: ProfileIn, user=Depends(get_current), db=Depends(db_session)):
        a = athlete_for(db, user, athlete_id)
        if body.level not in LEVELS or body.age_group not in AGE_GROUPS or body.consent_status not in CONSENT_STATES:
            raise HTTPException(422, "Choose a listed level, age group and consent status")
        if body.guardian_email and not EMAIL_RE.fullmatch(body.guardian_email):
            raise HTTPException(422, "Guardian e-mail is not valid")
        if is_minor(body.age_group) and not (body.guardian_name.strip() and
                                             (body.guardian_email.strip() or body.guardian_phone.strip())):
            raise HTTPException(422, "For an under-18 athlete, record the guardian's name and an e-mail or phone")
        p = profile_for(db, user, athlete_id)
        if p.consent_status != body.consent_status:
            p.consent_changed_at = now()
            # v1 gate for opening cases follows the recorded consent.
            a.service_consent_recorded_at = now() if body.consent_status == "recorded" else None
        for k, v in body.model_dump().items():
            setattr(p, k, v.strip() if isinstance(v, str) and k != "referral_notes" else v)
        p.updated_at = now()
        db.merge(p)
        ctx.event(db, user, "athlete.profile_updated", "athlete", a.id)
        db.commit()
        return profile_json(a, db.get(AthleteProfile, a.id))

    # ------------------------------------------------------------------ templates
    @app.get("/api/v2/note-templates")
    def templates(user=Depends(get_current), db=Depends(db_session)):
        return [{"id": t.id, "name": t.name, "body": t.body} for t in db.scalars(
            select(NoteTemplate).where(NoteTemplate.practitioner_id == user.id, NoteTemplate.active == True)  # noqa
            .order_by(NoteTemplate.name))]

    @app.post("/api/v2/note-templates", status_code=201)
    def new_template(body: TemplateIn, user=Depends(get_current), db=Depends(db_session)):
        t = NoteTemplate(tenant_id=user.tenant_id, practitioner_id=user.id, name=body.name.strip(), body=body.body)
        db.add(t)
        db.commit()
        return {"id": t.id}

    @app.post("/api/v2/note-templates/{template_id}/archive")
    def archive_template(template_id: int, user=Depends(get_current), db=Depends(db_session)):
        t = db.scalar(select(NoteTemplate).where(NoteTemplate.id == template_id, NoteTemplate.practitioner_id == user.id))
        if not t:
            raise HTTPException(404, "Template not found")
        t.active = False
        db.commit()
        return {"archived": True}

    # ------------------------------------------------------------------ drafts (autosave), sign, amend
    @app.get("/api/v2/note-drafts")
    def drafts(user=Depends(get_current), db=Depends(db_session)):
        return [{"client_draft_id": d.client_draft_id, "case_id": d.case_id, "note_date": d.note_date,
                 "content": d.content, "revision": d.revision, "updated_at": d.updated_at}
                for d in db.scalars(select(NoteDraft).where(NoteDraft.practitioner_id == user.id))]

    @app.put("/api/v2/note-drafts/{client_draft_id}")
    def save_draft(client_draft_id: str, body: DraftIn, user=Depends(get_current), db=Depends(db_session)):
        if not re.fullmatch(r"[A-Za-z0-9-]{8,40}", client_draft_id):
            raise HTTPException(422, "Invalid draft id")
        case_for(db, user, body.case_id)
        d = db.scalar(select(NoteDraft).where(NoteDraft.practitioner_id == user.id,
                                              NoteDraft.client_draft_id == client_draft_id))
        if d is None:
            if body.expected_revision != 0:
                raise HTTPException(409, "This draft was already sent or discarded")
            d = NoteDraft(tenant_id=user.tenant_id, practitioner_id=user.id, client_draft_id=client_draft_id,
                          case_id=body.case_id, note_date=body.note_date.isoformat(), content=body.content, revision=1)
            db.add(d)
        else:
            if body.expected_revision != d.revision:
                raise HTTPException(409, "A newer version of this draft was saved from another window")
            d.case_id, d.note_date, d.content = body.case_id, body.note_date.isoformat(), body.content
            d.revision += 1
            d.updated_at = now()
        db.commit()
        return {"client_draft_id": d.client_draft_id, "revision": d.revision, "saved_at": d.updated_at}

    def own_draft(db, user, client_draft_id) -> NoteDraft:
        d = db.scalar(select(NoteDraft).where(NoteDraft.practitioner_id == user.id,
                                              NoteDraft.client_draft_id == client_draft_id))
        if not d:
            raise HTTPException(404, "Draft not found")
        return d

    @app.post("/api/v2/note-drafts/{client_draft_id}/commit", status_code=201)
    def commit_draft(client_draft_id: str, user=Depends(get_current), db=Depends(db_session)):
        d = own_draft(db, user, client_draft_id)
        c = case_for(db, user, d.case_id)
        if len(d.content.strip()) < 5:
            raise HTTPException(422, "The note is too short")
        n = Note(tenant_id=user.tenant_id, case_id=c.id, author_id=user.id, note_date=d.note_date, content=d.content)
        db.add(n)
        db.delete(d)
        db.flush()
        ctx.event(db, user, "note.created", "note", n.id)
        db.commit()
        return {"id": n.id, "state": n.state, "revision": n.revision}

    @app.post("/api/v2/note-drafts/{client_draft_id}/discard")
    def discard_draft(client_draft_id: str, user=Depends(get_current), db=Depends(db_session)):
        db.delete(own_draft(db, user, client_draft_id))
        db.commit()
        return {"discarded": True}

    @app.get("/api/v2/offline-key")
    def get_offline_key(kid: str | None = None, user=Depends(get_current)):
        kid, key = offline_key(user, kid)
        return {"kid": kid, "key": base64.b64encode(key).decode()}

    def note_json(db, n: Note):
        sig = db.get(NoteSignature, n.id)
        amendments = db.scalars(select(NoteAmendment).where(NoteAmendment.note_id == n.id)
                                .order_by(NoteAmendment.id)).all()
        return {"id": n.id, "case_id": n.case_id, "note_date": n.note_date, "content": n.content, "state": n.state,
                "revision": n.revision, "locked": n.state == "final",
                "signed_at": sig.signed_at if sig else None,
                "integrity": (None if not sig else sig.content_sha256 == sha(n.content)),
                "amendments": [{"id": x.id, "content": x.content, "reason": x.reason, "created_at": x.created_at}
                               for x in amendments]}

    @app.get("/api/v2/notes/{note_id}")
    def get_note(note_id: int, user=Depends(get_current), db=Depends(db_session)):
        return note_json(db, own_note(db, user, note_id))

    @app.post("/api/v2/notes/{note_id}/sign")
    def sign_note(note_id: int, body: SignIn, user=Depends(get_current), db=Depends(db_session)):
        n = own_note(db, user, note_id)
        if n.state != "draft" or n.revision != body.expected_revision:
            raise HTTPException(409, "Note state or revision mismatch")
        n.state, n.revision = "final", n.revision + 1
        db.add(NoteSignature(note_id=n.id, tenant_id=user.tenant_id, practitioner_id=user.id,
                             content_sha256=sha(n.content)))
        ctx.event(db, user, "note.signed", "note", n.id)
        db.commit()
        return note_json(db, n)

    @app.post("/api/v2/notes/{note_id}/amendments", status_code=201)
    def amend_note(note_id: int, body: AmendIn, user=Depends(get_current), db=Depends(db_session)):
        n = own_note(db, user, note_id)
        if n.state != "final":
            raise HTTPException(409, "Only a signed (locked) note is amended; edit the draft instead")
        db.add(NoteAmendment(note_id=n.id, tenant_id=user.tenant_id, practitioner_id=user.id, content=body.content,
                             reason=body.reason.strip()))
        ctx.event(db, user, "note.amended", "note", n.id)
        db.commit()
        return note_json(db, n)

    # ------------------------------------------------------------------ 5Ps formulation
    def formulation_json(f):
        return {"version": f.version, "created_at": f.created_at, **json.loads(f.body)} if f else None

    @app.get("/api/v2/athletes/{athlete_id}/formulation")
    def get_formulation(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        rows = db.scalars(select(CaseFormulation).where(CaseFormulation.athlete_id == athlete_id,
                                                        CaseFormulation.practitioner_id == user.id)
                          .order_by(CaseFormulation.version.desc())).all()
        return {"sections": list(FIVE_PS), "current": formulation_json(rows[0]) if rows else None,
                "history": [{"version": r.version, "created_at": r.created_at} for r in rows[1:]],
                "authored_by": "practitioner"}

    @app.post("/api/v2/athletes/{athlete_id}/formulation", status_code=201)
    def save_formulation(athlete_id: int, body: FormulationIn, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        latest = db.scalar(select(func.max(CaseFormulation.version)).where(
            CaseFormulation.athlete_id == athlete_id, CaseFormulation.practitioner_id == user.id)) or 0
        if body.expected_version != latest:
            raise HTTPException(409, "The formulation was changed in another window; reload it first")
        sections = {k: getattr(body, k) for k in FIVE_PS}
        if not any(v.strip() for v in sections.values()):
            raise HTTPException(422, "Write at least one section")
        f = CaseFormulation(tenant_id=user.tenant_id, practitioner_id=user.id, athlete_id=athlete_id,
                            version=latest + 1, body=json.dumps(sections))
        db.add(f)
        ctx.event(db, user, "formulation.saved", "athlete", athlete_id)
        db.commit()
        return formulation_json(f)

    # ------------------------------------------------------------------ import
    def own_import(db, user, import_id) -> AthleteImport:
        imp = db.scalar(select(AthleteImport).where(AthleteImport.id == import_id,
                                                    AthleteImport.practitioner_id == user.id))
        if not imp:
            raise HTTPException(404, "Import not found")
        return imp

    @app.post("/api/v2/imports", status_code=201)
    def upload_import(body: ImportUploadIn, user=Depends(get_current), db=Depends(db_session)):
        try:
            raw = base64.b64decode(body.content_base64.split(",", 1)[-1], validate=True)
        except Exception:
            raise HTTPException(422, "The file could not be read") from None
        if len(raw) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "The file is larger than 2 MB")
        header, rows = parse_upload(body.filename, raw)
        imp = AthleteImport(tenant_id=user.tenant_id, practitioner_id=user.id, filename=body.filename[:160],
                            rows_total=len(rows), payload=json.dumps({"header": header, "rows": rows}))
        db.add(imp)
        db.commit()
        return {"id": imp.id, "columns": header, "sample": rows[:5], "rows_total": len(rows),
                "suggested_mapping": suggest_mapping(header), "targets": list(IMPORT_TARGETS),
                "required": list(REQUIRED_TARGETS), "levels": list(LEVELS), "age_groups": list(AGE_GROUPS)}

    @app.post("/api/v2/imports/{import_id}/preview")
    def preview_import(import_id: int, body: ImportMappingIn, user=Depends(get_current), db=Depends(db_session)):
        imp = own_import(db, user, import_id)
        data = json.loads(imp.payload)
        clean, errors = validate_rows(db, user, data["header"], data["rows"], body.mapping, body.scores)
        return {"rows_total": imp.rows_total, "valid_rows": len(clean), "errors": errors,
                "can_commit": not errors and bool(clean) and imp.status == "uploaded",
                "preview": [{k: v for k, v in r.items() if k != "scores"} | {"scores": len(r["scores"])}
                            for r in clean[:25]]}

    @app.post("/api/v2/imports/{import_id}/commit")
    def commit_import(import_id: int, body: ImportMappingIn, user=Depends(get_current), db=Depends(db_session)):
        imp = own_import(db, user, import_id)
        if imp.status != "uploaded":
            raise HTTPException(409, "This file was already imported or discarded")
        data = json.loads(imp.payload)
        clean, errors = validate_rows(db, user, data["header"], data["rows"], body.mapping, body.scores)
        if errors or not clean:
            # Never a partial import: every problem is reported and nothing is written.
            raise HTTPException(422, {"message": "Nothing was imported. Fix these rows and try again.",
                                      "errors": errors})
        try:
            for r in clean:
                a = Athlete(tenant_id=user.tenant_id, owner_practitioner_id=user.id, code=r["code"],
                            display_name=r["display_name"], sport=r["sport"], athlete_group=r["athlete_group"],
                            service_consent_recorded_at=now() if r["consent"] else None)
                db.add(a)
                db.flush()
                db.add(AthleteProfile(athlete_id=a.id, tenant_id=user.tenant_id, practitioner_id=user.id,
                                      level=r["level"], age_group=r["age_group"], guardian_name=r["guardian_name"],
                                      guardian_email=r["guardian_email"], guardian_phone=r["guardian_phone"],
                                      consent_status="recorded" if r["consent"] else "not_recorded",
                                      consent_changed_at=now() if r["consent"] else None,
                                      referral_source=r["referral_source"], referral_notes=""))
                for factor_id, value, d in r["scores"]:
                    db.add(Observation(tenant_id=user.tenant_id, athlete_id=a.id, case_id=None, factor_id=factor_id,
                                       value=value, observed_on=d, source="import", context="Imported"))
                from . import access
                access.note(user, a.id, "write")
            imp.status, imp.rows_imported = "committed", len(clean)
            imp.payload = json.dumps({"header": data["header"], "rows": []})  # the staged copy is not kept
            ctx.event(db, user, "athletes.imported", "import", imp.id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"imported": len(clean), "status": imp.status}

    @app.post("/api/v2/imports/{import_id}/discard")
    def discard_import(import_id: int, user=Depends(get_current), db=Depends(db_session)):
        imp = own_import(db, user, import_id)
        if imp.status == "uploaded":
            imp.status = "discarded"
            imp.payload = json.dumps({"header": [], "rows": []})
            db.commit()
        return {"status": imp.status}

    # ------------------------------------------------------------------ timeline
    @app.get("/api/v2/athletes/{athlete_id}/timeline")
    def timeline(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        cases = {c.id: c for c in db.scalars(select(Case).where(Case.athlete_id == athlete_id,
                                                                Case.owner_practitioner_id == user.id))}
        items = []
        for n in db.scalars(select(Note).where(Note.case_id.in_(list(cases) or [0]), Note.author_id == user.id)):
            items.append({"date": n.note_date, "kind": "note", "id": n.id, "title": cases[n.case_id].title,
                          "state": n.state, "text": n.content[:280]})
            for am in db.scalars(select(NoteAmendment).where(NoteAmendment.note_id == n.id)):
                items.append({"date": am.created_at.date().isoformat(), "kind": "amendment", "id": am.id,
                              "note_id": n.id, "title": "Amendment: " + am.reason, "text": am.content[:280]})
        factors = {f.id: f for f in db.scalars(select(Factor).where(Factor.tenant_id == user.tenant_id))}
        for o in db.scalars(select(Observation).where(Observation.athlete_id == athlete_id)):
            f = factors.get(o.factor_id)
            items.append({"date": o.observed_on, "kind": "observation", "id": o.id,
                          "title": f.label if f else "Measure", "value": o.value, "unit": f.unit if f else ""})
        for adm in db.scalars(select(AssessmentAdministration).where(AssessmentAdministration.athlete_id == athlete_id)):
            items.append({"date": adm.observed_on, "kind": "assessment", "id": adm.id, "title": adm.instrument_name})
        for asg in db.scalars(select(Assignment).where(Assignment.athlete_id == athlete_id)):
            iv = db.get(Intervention, asg.intervention_id)
            items.append({"date": asg.assigned_at.date().isoformat(), "kind": "intervention", "id": asg.id,
                          "title": iv.name if iv else "Intervention", "text": asg.dosage, "state": asg.status})
        for r in db.scalars(select(Report).where(Report.athlete_id == athlete_id)):
            items.append({"date": r.created_at.date().isoformat(), "kind": "report", "id": r.id, "title": r.title,
                          "state": r.status})
        for f in db.scalars(select(CaseFormulation).where(CaseFormulation.athlete_id == athlete_id,
                                                          CaseFormulation.practitioner_id == user.id)):
            items.append({"date": f.created_at.date().isoformat(), "kind": "formulation", "id": f.id,
                          "title": f"Case formulation v{f.version}"})
        for source in TIMELINE_SOURCES:
            items.extend(source(db, user, athlete_id))
        order = {"note": 0, "amendment": 1}
        items.sort(key=lambda x: (str(x["date"]), order.get(x["kind"], 2), x["id"]), reverse=True)
        return items


# ------------------------------------------------------------------ export and purge registration
def _export_rows(model, *where):
    def fn(db, user):
        from .trust import row_dict
        return [row_dict(r) for r in db.scalars(select(model).where(model.practitioner_id == user.id, *where))]
    return fn


def install_exporters():
    from .trust import EXPORTERS, PURGE_HOOKS
    EXPORTERS["athlete_profiles"] = _export_rows(AthleteProfile)
    EXPORTERS["note_templates"] = _export_rows(NoteTemplate)
    EXPORTERS["note_signatures"] = _export_rows(NoteSignature)
    EXPORTERS["note_amendments"] = _export_rows(NoteAmendment)
    EXPORTERS["note_drafts"] = _export_rows(NoteDraft)
    EXPORTERS["case_formulations"] = _export_rows(CaseFormulation)

    def purge(db, ids):
        note_ids = [n.id for n in db.scalars(select(Note).join(Case, Case.id == Note.case_id)
                                             .where(Case.athlete_id.in_(ids)))]
        case_ids = [c.id for c in db.scalars(select(Case).where(Case.athlete_id.in_(ids)))]
        for model, cond in ((NoteAmendment, NoteAmendment.note_id.in_(note_ids or [0])),
                            (NoteSignature, NoteSignature.note_id.in_(note_ids or [0])),
                            (NoteDraft, NoteDraft.case_id.in_(case_ids or [0])),
                            (CaseFormulation, CaseFormulation.athlete_id.in_(ids)),
                            (AthleteProfile, AthleteProfile.athlete_id.in_(ids))):
            db.query(model).filter(cond).delete(synchronize_session=False)
    if purge not in PURGE_HOOKS:
        PURGE_HOOKS.append(purge)


install_exporters()
