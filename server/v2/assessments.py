"""Phase 4: assessments over time.

* Instruments are data: items, response scale, scoring rule per subscale (sum or mean, reverse-scored items),
  interpretation bands, validation status and a licence field (own / open / licensed-score-entry-only).
* Practitioners build their own scales. Published tests are shipped as NAME AND SUBSCALE SCORE ENTRY ONLY: no item
  text exists in the database, the licence is ``licensed_score_entry_only`` and ``licence_confirmed`` is false
  until the owner confirms the licensing position (Admin Dashboard). Item text of a licensed instrument is never
  returned by any API: ``items_for`` is the only serializer and drops text whenever the licence is licensed.
* Trends are built for sparse data: every administration is a dated point; a subscale that was not scored at an
  administration is reported as missing; nothing is interpolated.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from ..models import now
from .context import Ctx, is_pg, require_admin
from .models import (LICENCES, VALIDATION, Instrument, InstrumentAdministration, InstrumentItem, InstrumentResponse,
                     InstrumentScore, InstrumentSubscale)

LICENSED = "licensed_score_entry_only"

# Published instruments: names and subscale score ranges only. No item text. Ranges are the commonly published
# scoring ranges and are flagged for the owner to confirm together with the licence (LAUNCH_CHECKLIST.md).
CATALOGUE = [
    {"name": "Competitive State Anxiety Inventory-2", "short_name": "CSAI-2", "subscales": [
        ("cognitive_anxiety", "Cognitive anxiety", 9, 36), ("somatic_anxiety", "Somatic anxiety", 9, 36),
        ("self_confidence", "Self-confidence", 9, 36)]},
    {"name": "Sport Motivation Scale", "short_name": "SMS-28", "subscales": [
        ("im_to_know", "Intrinsic motivation – to know", 4, 28),
        ("im_accomplishment", "Intrinsic motivation – toward accomplishment", 4, 28),
        ("im_stimulation", "Intrinsic motivation – to experience stimulation", 4, 28),
        ("em_identified", "Extrinsic motivation – identified", 4, 28),
        ("em_introjected", "Extrinsic motivation – introjected", 4, 28),
        ("em_external", "Extrinsic motivation – external regulation", 4, 28), ("amotivation", "Amotivation", 4, 28)]},
    {"name": "Profile of Mood States", "short_name": "POMS", "subscales": [
        ("tension", "Tension–anxiety", 0, 36), ("depression", "Depression–dejection", 0, 60),
        ("anger", "Anger–hostility", 0, 48), ("vigour", "Vigour–activity", 0, 32), ("fatigue", "Fatigue–inertia", 0, 28),
        ("confusion", "Confusion–bewilderment", 0, 28)]},
    {"name": "Athletic Coping Skills Inventory-28", "short_name": "ACSI-28", "subscales": [
        ("coping_adversity", "Coping with adversity", 0, 12), ("coachability", "Coachability", 0, 12),
        ("concentration", "Concentration", 0, 12), ("confidence_motivation", "Confidence and achievement motivation", 0, 12),
        ("goal_setting", "Goal setting and mental preparation", 0, 12), ("peaking", "Peaking under pressure", 0, 12),
        ("freedom_worry", "Freedom from worry", 0, 12), ("total", "Personal coping resources (total)", 0, 84)]},
]
CATALOGUE_NOTE = "Score entry only. Item text is not provided. Licensing to be confirmed by the platform owner."
# Later phases register callables(db, instrument) -> bool that may veto deactivation etc. (none yet).


# ------------------------------------------------------------------ scoring (pure functions, table-driven tests)
def band_for(score: float | None, bands: list[dict]) -> str:
    if score is None:
        return ""
    for b in bands:
        if b["min"] <= score <= b["max"]:
            return b["label"]
    return ""


def score_items(subscales: list[dict], items: list[dict], responses: dict[int, float], rmin: int, rmax: int) -> dict:
    """subscales: [{key, scoring, bands}], items: [{id, subscale_key, reverse_scored}], responses: {item_id: value}.
    A subscale with any unanswered item is missing (None). Key 'total' scores every item."""
    out = {}
    for s in subscales:
        members = items if s["key"] == "total" else [i for i in items if i["subscale_key"] == s["key"]]
        if not members or any(i["id"] not in responses for i in members):
            out[s["key"]] = {"score": None, "band": ""}
            continue
        values = []
        for i in members:
            v = float(responses[i["id"]])
            values.append(rmin + rmax - v if i["reverse_scored"] else v)
        score = sum(values) if s["scoring"] == "sum" else sum(values) / len(values)
        score = round(score, 2)
        out[s["key"]] = {"score": score, "band": band_for(score, s.get("bands") or [])}
    return out


# ------------------------------------------------------------------ request models
class BandIn(BaseModel):
    min: float
    max: float
    label: str = Field(min_length=1, max_length=80)


class SubscaleIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    scoring: str = Field(default="sum", pattern=r"^(sum|mean)$")
    bands: list[BandIn] = Field(default_factory=list, max_length=10)


class ItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    subscale: str
    reverse_scored: bool = False


class InstrumentIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    short_name: str = Field(default="", max_length=40)
    licence: str = "own"
    response_min: int = Field(default=1, ge=0, le=10)
    response_max: int = Field(default=5, ge=1, le=100)
    response_labels: list[str] = Field(default_factory=list, max_length=101)
    subscales: list[SubscaleIn] = Field(min_length=1, max_length=20)
    items: list[ItemIn] = Field(min_length=1, max_length=200)
    assignable_to_athlete: bool = False
    source_note: str = Field(default="", max_length=400)


class AdministrationIn(BaseModel):
    instrument_id: int
    administered_on: date
    responses: dict[int, float] | None = None
    subscale_scores: dict[str, float] | None = None
    note: str = Field(default="", max_length=500)


class LicenceIn(BaseModel):
    licence: str
    confirmed: bool


# ------------------------------------------------------------------ helpers
def ensure_catalogue(db, tenant_id: int) -> None:
    """SQLite/local only: PostgreSQL gets the same rows from migration 0036."""
    if is_pg(db):
        return
    have = set(db.scalars(select(Instrument.short_name).where(Instrument.tenant_id == tenant_id,
                                                              Instrument.practitioner_id.is_(None))))
    for spec in CATALOGUE:
        if spec["short_name"] in have:
            continue
        inst = Instrument(tenant_id=tenant_id, practitioner_id=None, name=spec["name"], short_name=spec["short_name"],
                          version="published", licence=LICENSED, licence_confirmed=False, validation_status="published",
                          response_min=0, response_max=0, assignable_to_athlete=False, source_note=CATALOGUE_NOTE)
        db.add(inst)
        db.flush()
        for pos, (key, label, lo, hi) in enumerate(spec["subscales"]):
            db.add(InstrumentSubscale(instrument_id=inst.id, key=key, label=label, scoring="sum", min_score=lo,
                                      max_score=hi, bands="[]", position=pos))
    db.commit()


def items_for(db, inst: Instrument) -> list[dict]:
    """The ONLY serializer for items. Licensed instruments never return item text."""
    rows = db.scalars(select(InstrumentItem).where(InstrumentItem.instrument_id == inst.id)
                      .order_by(InstrumentItem.position)).all()
    licensed = inst.licence == LICENSED
    return [{"id": i.id, "position": i.position, "subscale_key": i.subscale_key, "reverse_scored": i.reverse_scored,
             **({} if licensed else {"text": i.text})} for i in rows]


def subscales_for(db, inst) -> list[dict]:
    return [{"key": s.key, "label": s.label, "scoring": s.scoring, "min": s.min_score, "max": s.max_score,
             "bands": json.loads(s.bands or "[]")}
            for s in db.scalars(select(InstrumentSubscale).where(InstrumentSubscale.instrument_id == inst.id)
                                .order_by(InstrumentSubscale.position))]


def instrument_json(db, inst, *, with_items=True) -> dict:
    out = {"id": inst.id, "name": inst.name, "short_name": inst.short_name, "version": inst.version,
           "licence": inst.licence, "licence_confirmed": inst.licence_confirmed,
           "validation_status": inst.validation_status, "platform": inst.practitioner_id is None,
           "score_entry_only": inst.licence == LICENSED, "response_min": inst.response_min,
           "response_max": inst.response_max, "response_labels": json.loads(inst.response_labels or "[]"),
           "assignable_to_athlete": inst.assignable_to_athlete, "source_note": inst.source_note,
           "subscales": subscales_for(db, inst)}
    if with_items:
        out["items"] = items_for(db, inst)
    return out


def visible_instrument(db, user, instrument_id) -> Instrument:
    inst = db.scalar(select(Instrument).where(Instrument.id == instrument_id, Instrument.tenant_id == user.tenant_id,
                                              or_(Instrument.practitioner_id.is_(None),
                                                  Instrument.practitioner_id == user.id)))
    if not inst:
        raise HTTPException(404, "Instrument not found")
    return inst


def record_administration(db, user, athlete_id: int, inst: Instrument, body: AdministrationIn, source="practitioner"):
    """Validate and store one administration with its scores. Returns the administration."""
    subs = subscales_for(db, inst)
    if inst.licence == LICENSED or body.subscale_scores is not None:
        if inst.licence == LICENSED and body.responses:
            raise HTTPException(422, "This instrument accepts subscale scores only")
        given = body.subscale_scores or {}
        unknown = set(given) - {s["key"] for s in subs}
        if unknown or not given:
            raise HTTPException(422, "Enter at least one listed subscale score")
        for s in subs:
            if s["key"] in given and not s["min"] <= given[s["key"]] <= s["max"]:
                raise HTTPException(422, f"{s['label']} must be between {s['min']:g} and {s['max']:g}")
        scores = {s["key"]: {"score": round(float(given[s["key"]]), 2) if s["key"] in given else None,
                             "band": band_for(given.get(s["key"]), s["bands"])} for s in subs}
        mode = "subscale_entry"
    else:
        items = items_for(db, inst)
        ids = {i["id"] for i in items}
        resp = body.responses or {}
        if set(resp) - ids or not resp:
            raise HTTPException(422, "Answers must refer to this instrument's items")
        for v in resp.values():
            if not inst.response_min <= v <= inst.response_max or float(v) != int(v):
                raise HTTPException(422, f"Answers must be whole numbers {inst.response_min}-{inst.response_max}")
        scores = score_items(subs, items, resp, inst.response_min, inst.response_max)
        mode = "items"
    adm = InstrumentAdministration(tenant_id=user.tenant_id, practitioner_id=user.id, athlete_id=athlete_id,
                                   instrument_id=inst.id, administered_on=body.administered_on.isoformat(), mode=mode,
                                   source=source, note=body.note)
    db.add(adm)
    db.flush()
    if mode == "items":
        for item_id, v in (body.responses or {}).items():
            db.add(InstrumentResponse(administration_id=adm.id, item_id=item_id, value=float(v)))
    for key, sc in scores.items():
        db.add(InstrumentScore(administration_id=adm.id, subscale_key=key, score=sc["score"], band=sc["band"]))
    return adm, scores


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, athlete_for = ctx.db_session, ctx.get_current, ctx.athlete_for

    @app.get("/api/v2/instruments")
    def list_instruments(user=Depends(get_current), db=Depends(db_session)):
        ensure_catalogue(db, user.tenant_id)
        rows = db.scalars(select(Instrument).where(Instrument.tenant_id == user.tenant_id, Instrument.active == True,  # noqa
                                                   or_(Instrument.practitioner_id.is_(None),
                                                       Instrument.practitioner_id == user.id))
                          .order_by(Instrument.practitioner_id.is_(None).desc(), Instrument.name)).all()
        return [instrument_json(db, i, with_items=False) for i in rows]

    @app.get("/api/v2/instruments/{instrument_id}")
    def get_instrument(instrument_id: int, user=Depends(get_current), db=Depends(db_session)):
        return instrument_json(db, visible_instrument(db, user, instrument_id))

    @app.post("/api/v2/instruments", status_code=201)
    def build_instrument(body: InstrumentIn, user=Depends(get_current), db=Depends(db_session)):
        if body.licence not in ("own", "open"):
            raise HTTPException(422, "A practitioner-built scale is your own or an open instrument. Licensed tests "
                                     "are entered as subscale scores using the platform list.")
        if body.response_max <= body.response_min:
            raise HTTPException(422, "The highest answer must be above the lowest")
        if body.response_labels and len(body.response_labels) != body.response_max - body.response_min + 1:
            raise HTTPException(422, "Give one label per answer point, or none")
        keys = [s.key for s in body.subscales]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, "Subscale keys must be unique")
        for it in body.items:
            if it.subscale not in keys or it.subscale == "total":
                raise HTTPException(422, f"Item '{it.text[:30]}' needs one of the listed subscales")
        for s in body.subscales:
            n = len(body.items) if s.key == "total" else sum(1 for it in body.items if it.subscale == s.key)
            if not n:
                raise HTTPException(422, f"Subscale '{s.label}' has no items")
            lo = body.response_min * (n if s.scoring == "sum" else 1)
            hi = body.response_max * (n if s.scoring == "sum" else 1)
            for b in s.bands:
                if b.min > b.max or b.min < lo or b.max > hi:
                    raise HTTPException(422, f"Band '{b.label}' must lie within {lo:g}-{hi:g}")
        inst = Instrument(tenant_id=user.tenant_id, practitioner_id=user.id, name=body.name.strip(),
                          short_name=body.short_name.strip(), licence=body.licence, licence_confirmed=True,
                          validation_status="practitioner_defined", response_min=body.response_min,
                          response_max=body.response_max, response_labels=json.dumps(body.response_labels),
                          assignable_to_athlete=body.assignable_to_athlete, source_note=body.source_note)
        db.add(inst)
        db.flush()
        for pos, s in enumerate(body.subscales):
            n = len(body.items) if s.key == "total" else sum(1 for it in body.items if it.subscale == s.key)
            mult = n if s.scoring == "sum" else 1
            db.add(InstrumentSubscale(instrument_id=inst.id, key=s.key, label=s.label, scoring=s.scoring,
                                      min_score=body.response_min * mult, max_score=body.response_max * mult,
                                      bands=json.dumps([b.model_dump() for b in s.bands]), position=pos))
        for pos, it in enumerate(body.items, start=1):
            db.add(InstrumentItem(instrument_id=inst.id, position=pos, text=it.text.strip(), subscale_key=it.subscale,
                                  reverse_scored=it.reverse_scored))
        ctx.event(db, user, "instrument.created", "instrument", inst.id)
        db.commit()
        return instrument_json(db, inst)

    @app.post("/api/v2/instruments/{instrument_id}/archive")
    def archive_instrument(instrument_id: int, user=Depends(get_current), db=Depends(db_session)):
        inst = visible_instrument(db, user, instrument_id)
        if inst.practitioner_id != user.id:
            raise HTTPException(403, "Only your own scales can be archived")
        inst.active = False
        db.commit()
        return {"archived": True}

    @app.post("/api/v2/admin/instruments/{instrument_id}/licence")
    def owner_licence(instrument_id: int, body: LicenceIn, user=Depends(get_current), db=Depends(db_session)):
        require_admin(user)
        inst = db.scalar(select(Instrument).where(Instrument.id == instrument_id, Instrument.tenant_id == user.tenant_id,
                                                  Instrument.practitioner_id.is_(None)))
        if not inst:
            raise HTTPException(404, "Platform instrument not found")
        if body.licence not in LICENCES:
            raise HTTPException(422, "Unknown licence")
        # No item text is shipped, so changing the licence never reveals any; it records the owner's position.
        inst.licence, inst.licence_confirmed = body.licence, body.confirmed
        ctx.event(db, user, "instrument.licence_" + ("confirmed" if body.confirmed else "flagged"), "instrument", inst.id)
        db.commit()
        return instrument_json(db, inst)

    @app.post("/api/v2/athletes/{athlete_id}/instrument-administrations", status_code=201)
    def administer(athlete_id: int, body: AdministrationIn, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        inst = visible_instrument(db, user, body.instrument_id)
        if not inst.active:
            raise HTTPException(409, "This instrument is archived")
        adm, scores = record_administration(db, user, athlete_id, inst, body)
        ctx.event(db, user, "instrument.administered", "athlete", athlete_id)
        db.commit()
        return {"id": adm.id, "mode": adm.mode, "scores": scores}

    @app.get("/api/v2/athletes/{athlete_id}/instrument-administrations")
    def administrations(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete_for(db, user, athlete_id)
        out = []
        for adm in db.scalars(select(InstrumentAdministration).where(
                InstrumentAdministration.athlete_id == athlete_id, InstrumentAdministration.practitioner_id == user.id)
                .order_by(InstrumentAdministration.administered_on, InstrumentAdministration.id)):
            inst = db.get(Instrument, adm.instrument_id)
            out.append({"id": adm.id, "instrument_id": adm.instrument_id, "instrument": inst.short_name or inst.name,
                        "administered_on": adm.administered_on, "mode": adm.mode, "source": adm.source,
                        "scores": {s.subscale_key: {"score": s.score, "band": s.band} for s in db.scalars(
                            select(InstrumentScore).where(InstrumentScore.administration_id == adm.id))}})
        return out

    @app.get("/api/v2/athletes/{athlete_id}/instrument-trends/{instrument_id}")
    def trends(athlete_id: int, instrument_id: int, user=Depends(get_current), db=Depends(db_session)):
        """Every administration is a dated point. A subscale not scored on a date is listed as missing on that
        date. Nothing is interpolated; the response states it."""
        athlete_for(db, user, athlete_id)
        inst = visible_instrument(db, user, instrument_id)
        adms = db.scalars(select(InstrumentAdministration).where(
            InstrumentAdministration.athlete_id == athlete_id, InstrumentAdministration.practitioner_id == user.id,
            InstrumentAdministration.instrument_id == inst.id)
            .order_by(InstrumentAdministration.administered_on, InstrumentAdministration.id)).all()
        scores = {}
        for a in adms:
            for s in db.scalars(select(InstrumentScore).where(InstrumentScore.administration_id == a.id)):
                scores[(a.id, s.subscale_key)] = s
        series = []
        for sub in subscales_for(db, inst):
            points, missing = [], []
            for a in adms:
                s = scores.get((a.id, sub["key"]))
                if s is None or s.score is None:
                    missing.append(a.administered_on)
                else:
                    points.append({"date": a.administered_on, "score": s.score, "band": s.band,
                                   "administration_id": a.id})
            series.append({**sub, "points": points, "missing": missing})
        return {"instrument": instrument_json(db, inst, with_items=False),
                "dates": [a.administered_on for a in adms], "series": series, "interpolated": False}


# ------------------------------------------------------------------ timeline, export, purge
def _timeline(db, user, athlete_id):
    out = []
    for a in db.scalars(select(InstrumentAdministration).where(InstrumentAdministration.athlete_id == athlete_id,
                                                               InstrumentAdministration.practitioner_id == user.id)):
        inst = db.get(Instrument, a.instrument_id)
        out.append({"date": a.administered_on, "kind": "assessment", "id": 100000 + a.id,
                    "title": (inst.short_name or inst.name) + (" (completed by athlete)" if a.source == "athlete" else "")})
    return out


def install_hooks():
    from .record import TIMELINE_SOURCES
    from .trust import EXPORTERS, PURGE_HOOKS, row_dict
    if _timeline not in TIMELINE_SOURCES:
        TIMELINE_SOURCES.append(_timeline)

    def export_instruments(db, user):
        out = []
        for i in db.scalars(select(Instrument).where(Instrument.practitioner_id == user.id)):
            out.append({**row_dict(i), "subscales": subscales_for(db, i), "items": items_for(db, i)})
        return out

    def export_admins(db, user):
        out = []
        for a in db.scalars(select(InstrumentAdministration).where(InstrumentAdministration.practitioner_id == user.id)):
            out.append({**row_dict(a),
                        "responses": [row_dict(r) for r in db.scalars(select(InstrumentResponse).where(
                            InstrumentResponse.administration_id == a.id))],
                        "scores": [row_dict(s) for s in db.scalars(select(InstrumentScore).where(
                            InstrumentScore.administration_id == a.id))]})
        return out
    EXPORTERS["instruments"] = export_instruments
    EXPORTERS["instrument_administrations"] = export_admins

    def purge(db, ids):
        adm = [a.id for a in db.scalars(select(InstrumentAdministration).where(
            InstrumentAdministration.athlete_id.in_(ids)))]
        db.query(InstrumentResponse).filter(InstrumentResponse.administration_id.in_(adm or [0])).delete(
            synchronize_session=False)
        db.query(InstrumentScore).filter(InstrumentScore.administration_id.in_(adm or [0])).delete(
            synchronize_session=False)
        db.query(InstrumentAdministration).filter(InstrumentAdministration.athlete_id.in_(ids)).delete(
            synchronize_session=False)
    PURGE_HOOKS.insert(0, purge)


install_hooks()
