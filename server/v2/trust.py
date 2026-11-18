"""Phase 2: trust foundation — access log, data export, orderly closure, AI switch.

* Access log: practitioners read the log for their own athletes; the owner reads counts per practitioner only.
* Export: one ZIP with every record the requesting practitioner holds (v1 export + every v2 table registered in
  ``EXPORTERS``), after password re-authentication. Each export is recorded (hash and size, never content).
* Orderly closure (flag SPPS_PRACTITIONER_CLOSURE_ENABLED, docs/ACCOUNT_LIFECYCLE_DESIGN.md phase A3/A4): start
  (password), notice window, a disposition per athlete record, cancel, complete; the owner archive receives
  professional data only; the owner may restore within the cooling-off period and purge the erased records after it.
* AI: off unless SPPS_AI_FEATURES_ENABLED=true AND the practitioner has opted in (``require_ai``). With the global
  switch off the settings route answers 404 and the UI shows no AI wording.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from ..models import Athlete, Case, Practitioner, PractitionerProfile, RetentionHold, TermsAcceptance, now
from . import flags, guards
from .context import Ctx, is_pg, require_admin
from .models import (ClosureDisposition, DataExport, OwnerPractitionerArchive, PractitionerClosure,
                     PractitionerSetting, RecordAccess, RegistrationApplication, RegistrationTransition)

# name -> callable(db, user) -> list[dict]; later phases register their tables here so the export stays complete.
EXPORTERS: dict = {}
DISPOSITIONS = ("export_and_erase", "keep_deactivated")


def _aware(v):
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


def _jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    return v


def row_dict(obj, skip=()) -> dict:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in skip}


def notice_days() -> int:
    return max(0, int(os.getenv("SPPS_CLOSURE_NOTICE_DAYS", "30")))


def cooling_off_days() -> int:
    return max(0, int(os.getenv("SPPS_CLOSURE_COOLING_OFF_DAYS", "14")))


def require_ai(db, user) -> None:
    """The only gate for any AI code path. Raises 404 so a disabled feature is indistinguishable from absent."""
    if not flags.ai_globally_enabled():
        raise HTTPException(404, "Not found")
    setting = db.get(PractitionerSetting, user.id)
    if not setting or not setting.ai_opt_in:
        raise HTTPException(404, "Not found")


def owner_snapshot(db, auth, person, closure, counts) -> dict:
    """Professional data and a minimal closure record only. No athlete, case, note or assessment content."""
    profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == person.id))
    app_row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.practitioner_id == person.id))
    history = []
    if app_row:
        history = [{"from": t.from_state, "to": t.to_state, "actor": t.actor_kind, "at": _jsonable(t.happened_at)}
                   for t in db.scalars(select(RegistrationTransition).where(
                       RegistrationTransition.application_id == app_row.id).order_by(RegistrationTransition.id))]
    terms = [{"version": t.version, "accepted_at": _jsonable(t.accepted_at)} for t in auth.scalars(
        select(TermsAcceptance).where(TermsAcceptance.practitioner_id == person.id))] if not is_pg(db) else []
    return {"format": "spps-owner-archive-v1",
            "practitioner": {"id": person.id, "name": person.name, "email": person.email},
            "professional": {k: getattr(profile, k) for k in ("qualifications", "experience_years", "specialisms",
                                                              "achievements", "status")} if profile else {},
            "review_history": history, "terms_acceptances": terms,
            "closure": {"id": closure.id, "requested_at": _jsonable(closure.requested_at),
                        "notice_ends_at": _jsonable(closure.notice_ends_at), "completed_at": _jsonable(now()),
                        "dispositions": counts}}


def run_closure_hooks(app, db, user) -> None:
    """After a practitioner's closure completes, stop everything that could still share their records: report links
    are revoked, athlete-app report shares revoked and athlete-app links ended. Each module registers its own step."""
    import logging
    for hook in list(getattr(app.state, "v2_closure_hooks", [])):
        try:
            hook(db, user)
        except Exception:  # the closure itself has completed; log and continue with the other steps
            logging.getLogger("spps.v2").exception("closure follow-up step failed: %s", getattr(hook, "__name__", hook))


def add_closure_hook(app, hook) -> None:
    hooks = getattr(app.state, "v2_closure_hooks", None)
    if hooks is None:
        hooks = app.state.v2_closure_hooks = []
    if all(getattr(h, "__name__", None) != hook.__name__ for h in hooks):
        hooks.append(hook)


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, auth_session = ctx.db_session, ctx.get_current, ctx.auth_session

    # ------------------------------------------------------------------ access log
    @app.get("/api/v2/athletes/{athlete_id}/access-log")
    def athlete_access_log(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        ctx.athlete_for(db, user, athlete_id)
        rows = db.scalars(select(RecordAccess).where(RecordAccess.athlete_id == athlete_id,
                                                     RecordAccess.practitioner_id == user.id)
                          .order_by(RecordAccess.id.desc()).limit(500)).all()
        return [{"at": r.happened_at, "action": r.action, "resource": r.resource, "actor_id": r.actor_id,
                 "request_id": r.request_id} for r in rows]

    @app.get("/api/v2/admin/access-counts")
    def owner_access_counts(user=Depends(get_current), db=Depends(db_session), auth=Depends(auth_session)):
        """Owner view: numbers only. Never athlete ids, resources or content."""
        require_admin(user)
        if is_pg(db):
            rows = db.execute(text("SELECT * FROM spps_dev.v2_owner_access_counts()")).mappings().all()
            counts = [dict(r) for r in rows]
        else:
            q = (select(RecordAccess.practitioner_id, RecordAccess.action, func.count())
                 .where(RecordAccess.tenant_id == user.tenant_id)
                 .group_by(RecordAccess.practitioner_id, RecordAccess.action))
            per: dict[int, dict] = {}
            for pid, action, n in db.execute(q):
                per.setdefault(pid, {"practitioner_id": pid, "reads": 0, "writes": 0, "exports": 0})
                per[pid]["reads" if action == "read" else "writes" if action == "write" else "exports"] += n
            counts = list(per.values())
        names = {p.id: p.name for p in auth.scalars(select(Practitioner).where(Practitioner.tenant_id == user.tenant_id))}
        return [{**c, "name": names.get(c["practitioner_id"], "")} for c in sorted(counts, key=lambda c: c["practitioner_id"])]

    # ------------------------------------------------------------------ export
    def build_zip(db, user) -> tuple[bytes, int]:
        from ..account_lifecycle import build_export
        payload = build_export(db, user)
        for name, fn in EXPORTERS.items():
            payload["data"][name] = fn(db, user)
        payload["format"] = "spps-practitioner-export-v2"
        buf = io.BytesIO()
        files = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("export.json", json.dumps(payload, indent=2, default=str))
            files += 1
            for name, rows in payload["data"].items():
                if isinstance(rows, list) and rows:
                    out = io.StringIO()
                    writer = csv.DictWriter(out, fieldnames=sorted({k for r in rows for k in r}))
                    writer.writeheader()
                    for r in rows:
                        writer.writerow({k: ("'" + str(v)) if isinstance(v, str) and v[:1] in "=+-@" else v
                                         for k, v in r.items()})
                    z.writestr(f"csv/{name}.csv", out.getvalue().encode("utf-8-sig"))
                    files += 1
            z.writestr("README.txt", payload["notice"] + "\n\nexport.json holds every record; csv/ holds one "
                       "spreadsheet per record type. Excluded: " + "; ".join(payload["excluded"]) + ".\n")
            files += 1
        return buf.getvalue(), files

    @app.post("/api/v2/account/export")
    def account_export(body: "ReauthBody", user=Depends(get_current), db=Depends(db_session)):
        from ..account_lifecycle import ReauthIn, reauthenticate
        guards.hit("export", str(user.id))
        reauthenticate(db, user, ReauthIn(**body.model_dump()))
        raw, files = build_zip(db, user)
        digest = hashlib.sha256(raw).hexdigest()
        db.add(DataExport(tenant_id=user.tenant_id, practitioner_id=user.id, sha256=digest, size_bytes=len(raw),
                          files=files))
        db.add(RecordAccess(tenant_id=user.tenant_id, practitioner_id=user.id, actor_id=user.id, athlete_id=None,
                            action="export", resource="/api/v2/account/export"))
        ctx.event(db, user, "account.export_v2", "practitioner", user.id)
        db.commit()
        stamp = now().strftime("%Y%m%d-%H%M")
        return StreamingResponse(iter([raw]), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="spps-export-{stamp}.zip"', "Cache-Control": "no-store",
            "X-Export-SHA256": digest})

    @app.get("/api/v2/account/exports")
    def account_exports(user=Depends(get_current), db=Depends(db_session)):
        return [{"id": e.id, "created_at": e.created_at, "sha256": e.sha256, "size_bytes": e.size_bytes,
                 "files": e.files} for e in db.scalars(select(DataExport).where(
                     DataExport.practitioner_id == user.id).order_by(DataExport.id.desc()))]

    # ------------------------------------------------------------------ settings / AI
    @app.get("/api/v2/settings")
    def settings(user=Depends(get_current), db=Depends(db_session)):
        if not flags.ai_globally_enabled():
            return {"ai_available": False}  # no AI fields at all while the global switch is off
        s = db.get(PractitionerSetting, user.id)
        return {"ai_available": True, "ai_opt_in": bool(s and s.ai_opt_in),
                "ai_notice": "Optional drafting assistance. Every output is a labelled draft that you review; "
                             "nothing is written into a clinical record automatically."}

    @app.post("/api/v2/settings/ai")
    def set_ai(body: AiOptIn, user=Depends(get_current), db=Depends(db_session)):
        if not flags.ai_globally_enabled():
            raise HTTPException(404, "Not found")
        s = db.get(PractitionerSetting, user.id)
        if not s:
            s = PractitionerSetting(practitioner_id=user.id, tenant_id=user.tenant_id)
            db.add(s)
        s.ai_opt_in = body.opt_in
        s.ai_opt_in_at = now() if body.opt_in else None
        s.updated_at = now()
        ctx.event(db, user, "settings.ai_" + ("on" if body.opt_in else "off"), "practitioner", user.id)
        db.commit()
        return {"ai_opt_in": s.ai_opt_in}

    # ------------------------------------------------------------------ "Your data" page (one editable file)
    @app.get("/your-data")
    def your_data_page():
        from ..main import WEB
        return FileResponse(WEB / "your-data.html")

    # ------------------------------------------------------------------ orderly closure
    def closure_on():
        if not flags.closure_enabled():
            raise HTTPException(404, "Not found")

    def current_closure(db, user):
        return db.scalar(select(PractitionerClosure).where(PractitionerClosure.practitioner_id == user.id,
                                                           PractitionerClosure.state == "closing"))

    def precheck(db, user):
        athletes = db.scalars(select(Athlete).where(Athlete.owner_practitioner_id == user.id,
                                                    Athlete.tenant_id == user.tenant_id)).all()
        ids = [a.id for a in athletes]
        holds = db.scalar(select(func.count()).select_from(RetentionHold).where(
            RetentionHold.athlete_id.in_(ids or [0]), RetentionHold.active == True)) if ids else 0  # noqa: E712
        open_cases = db.scalar(select(func.count()).select_from(Case).where(
            Case.owner_practitioner_id == user.id, Case.status == "active"))
        return {"athletes": len(ids), "open_cases": open_cases or 0, "retention_holds": holds or 0}, athletes

    def closure_view(db, user, closure):
        pre, athletes = precheck(db, user)
        disp = {d.athlete_id: d.disposition for d in db.scalars(select(ClosureDisposition).where(
            ClosureDisposition.closure_id == closure.id))} if closure else {}
        exported = bool(closure and db.scalar(select(DataExport.id).where(
            DataExport.practitioner_id == user.id, DataExport.created_at >= closure.requested_at)))
        blockers = []
        if pre["retention_holds"]:
            blockers.append("An athlete record is under a retention hold")
        if closure and len(disp) < len(athletes):
            blockers.append("Choose what happens to every athlete record")
        if closure and "export_and_erase" in disp.values() and not exported:
            blockers.append("Export your data before erasing records")
        if closure and _aware(closure.notice_ends_at) > now():
            blockers.append("The notice window has not ended")
        return {"state": closure.state if closure else "none", "precheck": pre,
                "notice_ends_at": closure.notice_ends_at if closure else None,
                "dispositions": [{"athlete_id": a.id, "display_name": a.display_name,
                                  "disposition": disp.get(a.id)} for a in athletes],
                "exported_since_start": exported, "blockers": blockers, "can_complete": bool(closure) and not blockers}

    @app.get("/api/v2/account/closure")
    def closure_state(user=Depends(get_current), db=Depends(db_session)):
        closure_on()
        return closure_view(db, user, current_closure(db, user))

    @app.post("/api/v2/account/closure/start")
    def closure_start(body: "ReauthBody", user=Depends(get_current), db=Depends(db_session)):
        closure_on()
        from ..account_lifecycle import ReauthIn, reauthenticate
        reauthenticate(db, user, ReauthIn(**body.model_dump()))
        if user.is_admin:
            raise HTTPException(409, "The platform owner account cannot be closed here")
        if current_closure(db, user):
            raise HTTPException(409, "A closure is already in progress")
        c = PractitionerClosure(tenant_id=user.tenant_id, practitioner_id=user.id, state="closing",
                                notice_ends_at=now() + timedelta(days=notice_days()))
        db.add(c)
        ctx.event(db, user, "account.closure_started", "practitioner", user.id)
        db.commit()
        return closure_view(db, user, c)

    @app.post("/api/v2/account/closure/dispositions")
    def closure_disposition(body: DispositionIn, user=Depends(get_current), db=Depends(db_session)):
        closure_on()
        c = current_closure(db, user)
        if not c:
            raise HTTPException(409, "No closure in progress")
        if body.disposition not in DISPOSITIONS:
            raise HTTPException(422, "Unknown disposition")
        ctx.athlete_for(db, user, body.athlete_id)
        d = db.scalar(select(ClosureDisposition).where(ClosureDisposition.closure_id == c.id,
                                                       ClosureDisposition.athlete_id == body.athlete_id))
        if not d:
            d = ClosureDisposition(closure_id=c.id, tenant_id=user.tenant_id, practitioner_id=user.id,
                                   athlete_id=body.athlete_id, disposition=body.disposition)
            db.add(d)
        else:
            d.disposition = body.disposition
            d.recorded_at = now()
        db.commit()
        return closure_view(db, user, c)

    @app.post("/api/v2/account/closure/cancel")
    def closure_cancel(user=Depends(get_current), db=Depends(db_session)):
        closure_on()
        c = current_closure(db, user)
        if not c:
            raise HTTPException(409, "No closure in progress")
        c.state = "cancelled"
        ctx.event(db, user, "account.closure_cancelled", "practitioner", user.id)
        db.commit()
        return {"state": "cancelled"}

    @app.post("/api/v2/account/closure/complete")
    def closure_complete(user=Depends(get_current), db=Depends(db_session), auth=Depends(auth_session)):
        closure_on()
        c = current_closure(db, user)
        if not c:
            raise HTTPException(409, "No closure in progress")
        view = closure_view(db, user, c)
        if view["blockers"]:
            raise HTTPException(409, "; ".join(view["blockers"]))
        counts: dict[str, int] = {}
        for d in view["dispositions"]:
            counts[d["disposition"]] = counts.get(d["disposition"], 0) + 1
        if is_pg(db):
            try:
                db.execute(text("SELECT spps_dev.v2_complete_own_closure(:cid, CAST(:counts AS jsonb), :cool)"),
                           {"cid": c.id, "counts": json.dumps(counts), "cool": cooling_off_days()})
                db.commit()
            except DBAPIError as exc:
                db.rollback()
                raise HTTPException(409, "The closure could not be completed") from exc
            run_closure_hooks(app, db, user)
            return {"state": "closed"}
        person = auth.get(Practitioner, user.id)
        db.add(OwnerPractitionerArchive(tenant_id=user.tenant_id, practitioner_id=user.id, closure_id=c.id,
                                        snapshot=json.dumps(owner_snapshot(db, auth, person, c, counts))))
        c.state = "closed"
        c.completed_at = now()
        c.cooling_off_ends_at = now() + timedelta(days=cooling_off_days())
        person.active = False
        from .directory import remove_from_directory
        remove_from_directory(db, user.id)
        from ..models import LoginSession
        auth.query(LoginSession).filter(LoginSession.practitioner_id == user.id).update(
            {LoginSession.revoked: True}, synchronize_session=False)
        ctx.event(db, user, "account.closed", "practitioner", user.id)
        db.commit()
        if auth is not db:
            auth.commit()
        run_closure_hooks(app, db, user)
        return {"state": "closed"}

    @app.get("/api/v2/admin/closures")
    def owner_closures(user=Depends(get_current), db=Depends(db_session)):
        require_admin(user)
        out = []
        for c in db.scalars(select(PractitionerClosure).where(PractitionerClosure.tenant_id == user.tenant_id)
                            .order_by(PractitionerClosure.id.desc())):
            arch = db.scalar(select(OwnerPractitionerArchive).where(OwnerPractitionerArchive.closure_id == c.id))
            out.append({"id": c.id, "practitioner_id": c.practitioner_id, "state": c.state,
                        "requested_at": c.requested_at, "notice_ends_at": c.notice_ends_at,
                        "completed_at": c.completed_at, "cooling_off_ends_at": c.cooling_off_ends_at,
                        "archive": json.loads(arch.snapshot) if arch else None})
        return out

    def owner_closure(db, user, cid):
        require_admin(user)
        c = db.scalar(select(PractitionerClosure).where(PractitionerClosure.id == cid,
                                                        PractitionerClosure.tenant_id == user.tenant_id))
        if not c:
            raise HTTPException(404, "Closure not found")
        return c

    @app.post("/api/v2/admin/closures/{cid}/restore")
    def owner_restore(cid: int, user=Depends(get_current), db=Depends(db_session), auth=Depends(auth_session)):
        c = owner_closure(db, user, cid)
        if c.state != "closed" or _aware(c.cooling_off_ends_at) < now():
            raise HTTPException(409, "Only a closed account inside its cooling-off period can be restored")
        if is_pg(db):
            db.execute(text("SELECT spps_dev.v2_owner_restore_closure(:cid)"), {"cid": cid})
            db.commit()
        else:
            auth.get(Practitioner, c.practitioner_id).active = True
            c.state = "restored"
            db.commit()
        ctx.event(db, user, "account.closure_restored", "practitioner", c.practitioner_id)
        db.commit()
        return {"state": "restored"}

    @app.post("/api/v2/admin/closures/{cid}/purge")
    def owner_purge(cid: int, user=Depends(get_current), db=Depends(db_session)):
        """After cooling-off: erase the athlete records the practitioner chose to export and erase."""
        c = owner_closure(db, user, cid)
        if c.state != "closed" or _aware(c.cooling_off_ends_at) > now():
            raise HTTPException(409, "Records can be purged only after the cooling-off period")
        if is_pg(db):
            n = db.execute(text("SELECT spps_dev.v2_owner_purge_closure(:cid)"), {"cid": cid}).scalar()
            db.commit()
        else:
            n = purge_orm(db, c)
        ctx.event(db, user, "account.closure_purged", "practitioner", c.practitioner_id)
        db.commit()
        return {"state": "purged", "erased_records": n}

    def purge_orm(db, c) -> int:
        from ..models import (AssessmentAdministration, AssessmentSubscore, Assignment, InjuryCheckIn,
                              InjuryEpisode, Inquiry, Note, Observation, Report)
        ids = [d.athlete_id for d in db.scalars(select(ClosureDisposition).where(
            ClosureDisposition.closure_id == c.id, ClosureDisposition.disposition == "export_and_erase"))]
        if ids:
            for fn in PURGE_HOOKS:
                fn(db, ids)
            admin_ids = [a.id for a in db.scalars(select(AssessmentAdministration).where(
                AssessmentAdministration.athlete_id.in_(ids)))]
            ep_ids = [e.id for e in db.scalars(select(InjuryEpisode).where(InjuryEpisode.athlete_id.in_(ids)))]
            case_ids = [x.id for x in db.scalars(select(Case).where(Case.athlete_id.in_(ids)))]
            for model, cond in ((AssessmentSubscore, AssessmentSubscore.administration_id.in_(admin_ids or [0])),
                                (AssessmentAdministration, AssessmentAdministration.athlete_id.in_(ids)),
                                (InjuryCheckIn, InjuryCheckIn.episode_id.in_(ep_ids or [0])),
                                (InjuryEpisode, InjuryEpisode.athlete_id.in_(ids)),
                                (Observation, Observation.athlete_id.in_(ids)),
                                (Note, Note.case_id.in_(case_ids or [0])),
                                (Assignment, Assignment.athlete_id.in_(ids)),
                                (Report, Report.athlete_id.in_(ids)),
                                (Case, Case.athlete_id.in_(ids))):
                db.query(model).filter(cond).delete(synchronize_session=False)
            db.query(Inquiry).filter(Inquiry.linked_athlete_id.in_(ids)).update(
                {Inquiry.linked_athlete_id: None}, synchronize_session=False)
            db.query(Athlete).filter(Athlete.id.in_(ids)).delete(synchronize_session=False)
        c.state = "purged"
        return len(ids)


# Later phases add callables(db, athlete_ids) that delete their own rows for erased athletes (SQLite path; the
# PostgreSQL purge function is replaced by each later migration that adds athlete-linked tables).
PURGE_HOOKS: list = []


class AiOptIn(BaseModel):
    opt_in: bool


class DispositionIn(BaseModel):
    athlete_id: int
    disposition: str


class ReauthBody(BaseModel):
    password: str | None = None
    supabase_access_token: str | None = None
