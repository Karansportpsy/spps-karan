"""Phase 5: audience redaction on the server, sign-before-share, expiring/revocable consented links."""
from __future__ import annotations

import io

import pytest
from sqlalchemy import text

from .conftest import PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete

CLINICAL = "CLINICAL-ONLY-MARKER fictional formulation detail"
MEDICAL = "MEDICAL-ONLY-MARKER fictional sleep referral"
COACH = "COACH-VISIBLE fictional: keep pre-match routine consistent"


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages)


@pytest.fixture
def setup(app, v2env):
    v2env.setenv("SPPS_REPORT_LINKS_ENABLED", "true")
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    csai = next(i for i in b.get("/api/v2/instruments").json() if i["short_name"] == "CSAI-2")
    b.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": csai["id"], "administered_on": "2026-10-01", "subscale_scores": {"self_confidence": 24}},
        headers=hb)
    tpl = b.post("/api/v2/report-templates", json={"name": "Squad update", "fields": [
        {"key": "summary", "label": "Summary for the coach", "kind": "narrative", "audiences": ["coach", "athlete", "medical"]},
        {"key": "clinical", "label": "Clinical formulation", "kind": "narrative", "audiences": []},
        {"key": "medical", "label": "Medical referral", "kind": "narrative", "audiences": ["medical"]},
        {"key": "confidence", "label": "Confidence trend", "kind": "chart", "audiences": ["coach", "medical"],
         "instrument_id": csai["id"]}]}, headers=hb)
    assert tpl.status_code == 201, tpl.text
    return b, hb, aid, tpl.json()["id"]


def _report(b, hb, aid, tid, audience="coach"):
    r = b.post(f"/api/v2/athletes/{aid}/v2-reports", json={"template_id": tid, "audience": audience,
                                                           "title": "Fictional October update"}, headers=hb).json()
    r = b.put(f"/api/v2/v2-reports/{r['id']}", json={"values": {"summary": COACH, "clinical": CLINICAL,
                                                                "medical": MEDICAL}, "expected_revision": 1},
              headers=hb).json()
    return r


def test_coach_report_never_contains_clinical_only_or_medical_fields(setup):
    b, hb, aid, tid = setup
    r = _report(b, hb, aid, tid)
    included = {f["key"]: f["included"] for f in r["fields"]}
    assert included == {"summary": True, "clinical": False, "medical": False, "confidence": True}
    preview = b.get(f"/api/v2/v2-reports/{r['id']}/preview").text
    assert COACH in preview and CLINICAL not in preview and MEDICAL not in preview
    assert b.get(f"/api/v2/v2-reports/{r['id']}/pdf").status_code == 409  # not signed yet
    signed = b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hb)
    assert signed.status_code == 200 and signed.json()["status"] == "signed"
    rendered = b.get(f"/api/v2/v2-reports/{r['id']}/rendered")
    pdf = b.get(f"/api/v2/v2-reports/{r['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    body = pdf_text(pdf.content)
    for out in (rendered.text, body):
        assert "keep pre-match routine" in out and "CLINICAL-ONLY-MARKER" not in out and "MEDICAL-ONLY-MARKER" not in out
    assert "Self-confidence" in rendered.text and "24" in rendered.text  # scores pulled in automatically
    consent = b.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "coach", "given_by": "athlete"},
                     headers=hb)
    link = b.post(f"/api/v2/v2-reports/{r['id']}/links", json={"expires_in_days": 7}, headers=hb)
    assert consent.status_code == 201 and link.status_code == 201, link.text
    token = link.json()["url"].rsplit("/r/", 1)[1]
    page = fresh_client(b.app).get(f"/r/{token}")
    assert page.status_code == 200 and "keep pre-match routine" in page.text
    assert "CLINICAL-ONLY-MARKER" not in page.text and "MEDICAL-ONLY-MARKER" not in page.text
    assert "<svg" in page.text and "missing" not in page.text.split("Confidence trend")[1][:40]


def test_medical_report_includes_medical_but_still_not_clinical_only(setup):
    b, hb, aid, tid = setup
    r = _report(b, hb, aid, tid, audience="medical")
    b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hb)
    out = b.get(f"/api/v2/v2-reports/{r['id']}/rendered").text
    assert "MEDICAL-ONLY-MARKER" in out and "CLINICAL-ONLY-MARKER" not in out


def test_signed_report_is_frozen(setup):
    b, hb, aid, tid = setup
    r = _report(b, hb, aid, tid)
    b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hb)
    assert b.put(f"/api/v2/v2-reports/{r['id']}", json={"values": {"summary": "changed"}, "expected_revision": 3},
                 headers=hb).status_code == 409
    assert b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 3}, headers=hb).status_code == 409


def test_link_needs_signature_and_recorded_consent_for_that_audience(setup):
    b, hb, aid, tid = setup
    r = _report(b, hb, aid, tid)
    assert b.post(f"/api/v2/v2-reports/{r['id']}/links", json={}, headers=hb).status_code == 409  # unsigned
    b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hb)
    res = b.post(f"/api/v2/v2-reports/{r['id']}/links", json={}, headers=hb)
    assert res.status_code == 409 and "consent" in res.text
    b.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "medical", "given_by": "athlete"}, headers=hb)
    assert b.post(f"/api/v2/v2-reports/{r['id']}/links", json={}, headers=hb).status_code == 409  # wrong audience
    assert b.post(f"/api/v2/v2-reports/{r['id']}/links", json={"expires_in_days": 90}, headers=hb).status_code == 422


def test_under_18_needs_the_guardians_consent(setup):
    b, hb, aid, tid = setup
    b.put(f"/api/v2/athletes/{aid}/profile", json={"age_group": "15-17", "guardian_name": "Fictional Parent",
                                                   "guardian_phone": "+910000000001", "consent_status": "recorded"},
          headers=hb)
    assert b.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "coach", "given_by": "athlete"},
                  headers=hb).status_code == 422
    assert b.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "coach", "given_by": "guardian"},
                  headers=hb).status_code == 201


def _shared(b, hb, aid, tid):
    r = _report(b, hb, aid, tid)
    b.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hb)
    cid = b.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "coach", "given_by": "athlete"},
                 headers=hb).json()["id"]
    link = b.post(f"/api/v2/v2-reports/{r['id']}/links", json={"expires_in_days": 7}, headers=hb).json()
    return r, cid, link, link["url"].rsplit("/r/", 1)[1]


def test_expired_revoked_withdrawn_or_unknown_links_return_nothing_and_every_view_is_audited(app, setup):
    b, hb, aid, tid = setup
    anon = fresh_client(app)
    r, cid, link, token = _shared(b, hb, aid, tid)
    assert anon.get(f"/api/v2/report-links/{token}").status_code == 200
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE report_links SET expires_at='2020-01-01 00:00:00' WHERE id=:i"), {"i": link["id"]})
    for path in (f"/api/v2/report-links/{token}", f"/r/{token}"):
        res = anon.get(path)
        assert res.status_code == 404 and "keep pre-match routine" not in res.text
    r2, cid2, link2, token2 = _shared(b, hb, aid, tid)
    assert b.post(f"/api/v2/report-links/{link2['id']}/revoke", headers=hb).status_code == 200
    assert anon.get(f"/api/v2/report-links/{token2}").status_code == 404
    r3, cid3, link3, token3 = _shared(b, hb, aid, tid)
    b.post(f"/api/v2/report-consents/{cid3}/withdraw", headers=hb)
    assert anon.get(f"/api/v2/report-links/{token3}").status_code == 404
    assert anon.get("/api/v2/report-links/" + "z" * 43).status_code == 404
    views = {lk["id"]: [v["outcome"] for v in lk["views"]] for lk in b.get(f"/api/v2/v2-reports/{r['id']}/links").json()}
    assert views[link["id"]] == ["shown", "expired", "expired"]
    assert [v["outcome"] for v in b.get(f"/api/v2/v2-reports/{r2['id']}/links").json()[0]["views"]] == ["revoked"]
    assert [v["outcome"] for v in b.get(f"/api/v2/v2-reports/{r3['id']}/links").json()[0]["views"]] == ["withdrawn"]


def test_links_are_off_unless_the_flag_is_on(app, setup, v2env):
    b, hb, aid, tid = setup
    r, cid, link, token = _shared(b, hb, aid, tid)
    v2env.setenv("SPPS_REPORT_LINKS_ENABLED", "false")
    assert fresh_client(app).get(f"/r/{token}").status_code == 404
    assert b.post(f"/api/v2/v2-reports/{r['id']}/links", json={}, headers=hb).status_code == 404


def test_reports_templates_consents_and_links_are_private(app, setup):
    b, hb, aid, tid = setup
    r, cid, link, token = _shared(b, hb, aid, tid)
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    caid = make_athlete(c, hc, code="C-9")
    checks = [c.get(f"/api/v2/v2-reports/{r['id']}"), c.get(f"/api/v2/v2-reports/{r['id']}/preview"),
              c.get(f"/api/v2/v2-reports/{r['id']}/rendered"), c.get(f"/api/v2/v2-reports/{r['id']}/pdf"),
              c.get(f"/api/v2/v2-reports/{r['id']}/links"), c.get(f"/api/v2/athletes/{aid}/v2-reports"),
              c.get(f"/api/v2/athletes/{aid}/report-consents"),
              c.post(f"/api/v2/report-links/{link['id']}/revoke", headers=hc),
              c.post(f"/api/v2/report-consents/{cid}/withdraw", headers=hc),
              c.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 3}, headers=hc),
              c.post(f"/api/v2/athletes/{caid}/v2-reports", json={"template_id": tid, "audience": "coach",
                                                                  "title": "Using B's template"}, headers=hc)]
    assert [x.status_code for x in checks] == [404] * len(checks)
    assert c.get("/api/v2/report-templates").json() == []
