"""Phase 4: scoring rules (table-driven), the builder, licensed score-entry-only instruments, sparse trends."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from server.v2.assessments import band_for, score_items

from .conftest import OWNER_LOGIN, PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete

ITEMS = [{"id": 1, "subscale_key": "a", "reverse_scored": False}, {"id": 2, "subscale_key": "a", "reverse_scored": True},
         {"id": 3, "subscale_key": "b", "reverse_scored": False}, {"id": 4, "subscale_key": "b", "reverse_scored": False}]
BANDS = [{"min": 2, "max": 4, "label": "low"}, {"min": 5, "max": 7, "label": "mid"}, {"min": 8, "max": 10, "label": "high"}]


@pytest.mark.parametrize("scoring,responses,rmin,rmax,expected", [
    # sum, item 2 reverse-scored on 1..5: 4 + (1+5-2)=8
    ("sum", {1: 4, 2: 2, 3: 1, 4: 5}, 1, 5, {"a": 8, "b": 6, "total": 14}),
    # mean of the same
    ("mean", {1: 4, 2: 2, 3: 1, 4: 5}, 1, 5, {"a": 4.0, "b": 3.0, "total": 3.5}),
    # reverse on a 0..3 scale: 3 + (0+3-3)=3
    ("sum", {1: 3, 2: 3, 3: 0, 4: 0}, 0, 3, {"a": 3, "b": 0, "total": 3}),
    # a missing item makes its subscale (and the total) missing, never imputed
    ("sum", {1: 4, 2: 2, 3: 1}, 1, 5, {"a": 8, "b": None, "total": None}),
    ("mean", {}, 1, 5, {"a": None, "b": None, "total": None}),
    # rounding to two places for means
    ("mean", {1: 1, 2: 4, 3: 2, 4: 3}, 1, 5, {"a": 1.5, "b": 2.5, "total": 2.0}),
])
def test_scoring_rules(scoring, responses, rmin, rmax, expected):
    subs = [{"key": k, "scoring": scoring, "bands": []} for k in ("a", "b", "total")]
    got = score_items(subs, ITEMS, responses, rmin, rmax)
    assert {k: v["score"] for k, v in got.items()} == expected


@pytest.mark.parametrize("score,label", [(None, ""), (1.9, ""), (2, "low"), (4, "low"), (4.5, ""), (5, "mid"),
                                         (7, "mid"), (8, "high"), (10, "high"), (10.1, "")])
def test_interpretation_bands_are_inclusive_and_never_guess(score, label):
    assert band_for(score, BANDS) == label


CUSTOM = {"name": "Fictional Pre-Match Readiness Scale", "short_name": "FPRS", "licence": "own",
          "response_min": 1, "response_max": 5, "response_labels": ["Never", "Rarely", "Sometimes", "Often", "Always"],
          "subscales": [{"key": "focus", "label": "Focus", "scoring": "sum",
                         "bands": [{"min": 2, "max": 5, "label": "Low"}, {"min": 6, "max": 10, "label": "High"}]},
                        {"key": "calm", "label": "Calm", "scoring": "mean"},
                        {"key": "total", "label": "Total", "scoring": "sum"}],
          "items": [{"text": "Fictional item: I can focus on my task.", "subscale": "focus"},
                    {"text": "Fictional item: I get distracted.", "subscale": "focus", "reverse_scored": True},
                    {"text": "Fictional item: I feel calm.", "subscale": "calm"},
                    {"text": "Fictional item: My breathing is steady.", "subscale": "calm"}],
          "assignable_to_athlete": True}


def test_custom_instrument_scores_correctly_through_the_api(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    inst = b.post("/api/v2/instruments", json=CUSTOM, headers=hb)
    assert inst.status_code == 201, inst.text
    body = inst.json()
    items = {i["position"]: i["id"] for i in body["items"]}
    assert body["subscales"][0]["min"] == 2 and body["subscales"][0]["max"] == 10  # 2 items x 1..5
    aid = make_athlete(b, hb)
    res = b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": body["id"], "administered_on": "2026-11-01",
        "responses": {str(items[1]): 5, str(items[2]): 2, str(items[3]): 4, str(items[4]): 5}}, headers=hb)
    assert res.status_code == 201, res.text
    scores = res.json()["scores"]
    assert scores["focus"] == {"score": 9.0, "band": "High"}  # 5 + (1+5-2)
    assert scores["calm"]["score"] == 4.5 and scores["total"]["score"] == 18.0
    bad = b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": body["id"], "administered_on": "2026-11-02", "responses": {str(items[1]): 9}}, headers=hb)
    assert bad.status_code == 422


@pytest.mark.parametrize("change,message", [
    ({"licence": "licensed_score_entry_only"}, "own or an open"),
    ({"response_min": 5, "response_max": 5}, "highest answer"),
    ({"response_labels": ["a", "b"]}, "one label per answer"),
    ({"items": [{"text": "x", "subscale": "nope"}]}, "needs one of the listed subscales"),
    ({"subscales": [{"key": "focus", "label": "Focus", "bands": [{"min": 0, "max": 99, "label": "x"}]},
                    {"key": "calm", "label": "Calm"}]}, "must lie within"),
])
def test_builder_refuses_inconsistent_definitions(app, change, message):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    res = b.post("/api/v2/instruments", json={**CUSTOM, **change}, headers=hb)
    assert res.status_code == 422 and message in res.text


def _licensed(b):
    return next(i for i in b.get("/api/v2/instruments").json() if i["short_name"] == "CSAI-2")


def test_published_tests_ship_as_score_entry_only_and_flagged_for_the_owner(app):
    b = fresh_client(app)
    login(b, PRACTITIONER_B)
    names = {i["short_name"]: i for i in b.get("/api/v2/instruments").json()}
    for short in ("CSAI-2", "SMS-28", "POMS", "ACSI-28"):
        inst = names[short]
        assert inst["licence"] == "licensed_score_entry_only" and inst["score_entry_only"]
        assert inst["licence_confirmed"] is False and inst["assignable_to_athlete"] is False
        assert inst["subscales"] and inst["platform"]
    detail = b.get(f"/api/v2/instruments/{names['CSAI-2']['id']}").json()
    assert detail["items"] == []


def test_licensed_instrument_never_exposes_item_text_through_any_api(app):
    """Even if item text were somehow present in the database, no response carries it."""
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    lic = _licensed(b)
    from server.v2.models import InstrumentItem
    marker = "FICTIONAL-LICENSED-ITEM-TEXT-MARKER"
    with app.state.DB() as db:  # simulate a stray row written behind the API's back
        db.add(InstrumentItem(instrument_id=lic["id"], position=1, text=marker, subscale_key="cognitive_anxiety"))
        db.commit()
    aid = make_athlete(b, hb)
    assert b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": lic["id"], "administered_on": "2026-11-01", "responses": {"1": 3}}, headers=hb).status_code == 422
    entered = b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": lic["id"], "administered_on": "2026-11-01",
        "subscale_scores": {"cognitive_anxiety": 20, "self_confidence": 30}}, headers=hb)
    assert entered.status_code == 201 and entered.json()["scores"]["somatic_anxiety"]["score"] is None
    assert b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": lic["id"], "administered_on": "2026-11-02",
        "subscale_scores": {"cognitive_anxiety": 99}}, headers=hb).status_code == 422
    responses = [b.get("/api/v2/instruments"), b.get(f"/api/v2/instruments/{lic['id']}"),
                 b.get(f"/api/v2/athletes/{aid}/instrument-administrations"),
                 b.get(f"/api/v2/athletes/{aid}/instrument-trends/{lic['id']}"),
                 b.get(f"/api/v2/athletes/{aid}/timeline")]
    owner = fresh_client(app)
    ho = login(owner, OWNER_LOGIN)
    responses.append(owner.post(f"/api/v2/admin/instruments/{lic['id']}/licence",
                                json={"licence": "licensed_score_entry_only", "confirmed": True}, headers=ho))
    exp = b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    z = zipfile.ZipFile(io.BytesIO(exp.content))
    blobs = [r.text for r in responses] + [z.read(n).decode("utf-8", "ignore") for n in z.namelist()]
    assert all(r.status_code == 200 for r in responses)
    assert not any(marker in blob for blob in blobs)
    assert responses[-1].json()["licence_confirmed"] is True


def test_only_the_owner_sets_platform_licences(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    lic = _licensed(b)
    assert b.post(f"/api/v2/admin/instruments/{lic['id']}/licence", json={"licence": "own", "confirmed": True},
                  headers=hb).status_code == 403


def test_trends_show_every_dated_point_and_mark_missing_without_interpolation(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    lic = _licensed(b)
    aid = make_athlete(b, hb)
    for d, scores in (("2026-01-10", {"cognitive_anxiety": 20, "self_confidence": 25}),
                      ("2026-03-02", {"self_confidence": 27}),
                      ("2026-09-15", {"cognitive_anxiety": 14, "self_confidence": 31})):
        assert b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
            "instrument_id": lic["id"], "administered_on": d, "subscale_scores": scores}, headers=hb).status_code == 201
    t = b.get(f"/api/v2/athletes/{aid}/instrument-trends/{lic['id']}").json()
    assert t["interpolated"] is False and t["dates"] == ["2026-01-10", "2026-03-02", "2026-09-15"]
    cog = next(s for s in t["series"] if s["key"] == "cognitive_anxiety")
    assert [p["date"] for p in cog["points"]] == ["2026-01-10", "2026-09-15"] and cog["missing"] == ["2026-03-02"]
    som = next(s for s in t["series"] if s["key"] == "somatic_anxiety")
    assert som["points"] == [] and len(som["missing"]) == 3


def test_instruments_and_scores_are_private_to_each_practitioner(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    inst = b.post("/api/v2/instruments", json=CUSTOM, headers=hb).json()
    aid = make_athlete(b, hb)
    lic = _licensed(b)
    b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": lic["id"], "administered_on": "2026-11-01", "subscale_scores": {"self_confidence": 20}},
        headers=hb)
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    assert all(i["id"] != inst["id"] for i in c.get("/api/v2/instruments").json())
    assert c.get(f"/api/v2/instruments/{inst['id']}").status_code == 404
    assert c.post(f"/api/v2/instruments/{inst['id']}/archive", headers=hc).status_code == 404
    assert c.get(f"/api/v2/athletes/{aid}/instrument-administrations").status_code == 404
    assert c.get(f"/api/v2/athletes/{aid}/instrument-trends/{lic['id']}").status_code == 404
    caid = make_athlete(c, hc, code="C-1")
    assert c.post(f"/api/v2/athletes/{caid}/instrument-administrations", json={
        "instrument_id": inst["id"], "administered_on": "2026-11-01", "responses": {"1": 1}}, headers=hc).status_code == 404


def test_assignable_flag_is_stored_and_never_set_on_licensed_instruments(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    inst = b.post("/api/v2/instruments", json=CUSTOM, headers=hb).json()
    assert inst["assignable_to_athlete"] is True
    assert all(not i["assignable_to_athlete"] for i in b.get("/api/v2/instruments").json() if i["score_entry_only"])
    exported = b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    data = json.loads(zipfile.ZipFile(io.BytesIO(exported.content)).read("export.json"))
    assert data["data"]["instruments"][0]["items"][0]["text"].startswith("Fictional item")  # own scale: text is yours
