"""Stage 13A.6: dependants, guardian authority, independent review, consent ledger.

Permission-matrix negative cases 5, 6, 7 (consent side), 12, 13 plus reviewer
separation of duties. Real PostgreSQL (ephemeral), fictional data only.
"""
import pytest
from sqlalchemy import create_engine, text

from tests_integration.test_portal_auth import client, verified_login
from tools.identity_ops import grant_reviewer
from tools.identity_review_db import EPHEMERAL_TARGET

ALL_SCOPES = ["appointment_metadata", "assigned_tasks", "consent_management"]


def reviewer(app, env):
    c, address = verified_login(app, env, role="athlete")
    grant_reviewer(EPHEMERAL_TARGET.admin_url(), address)
    assert "reviewer" in c.get("/api/portal/auth/me").json()["roles"]
    return c


def guardian_with_dependant(app, env, name="Fictional Dependant"):
    g, _ = verified_login(app, env, role="guardian")
    r = g.post("/api/portal/dependants", json={"display_name": name})
    assert r.status_code == 201, r.text
    return g, r.json()["person_id"]


def verified_authority(app, env, scopes=ALL_SCOPES):
    g, person = guardian_with_dependant(app, env)
    r = g.post("/api/portal/guardian-authorities", json={"dependant_person_id": person, "scopes": scopes,
                                                        "evidence_reference": "Fictional birth record ref FX-1"})
    assert r.status_code == 201 and r.json()["status"] == "PENDING_VERIFICATION"
    aid = r.json()["authority_id"]
    rev = reviewer(app, env)
    assert rev.post(f"/api/portal/review/authorities/{aid}/decision",
                    json={"decision": "VERIFIED", "reason": "Fictional document checked", "valid_days": 30}).status_code == 200
    return g, person, aid, rev


def test_full_guardian_flow_evidence_review_and_projection(app, integration_env):
    g, person = guardian_with_dependant(app, integration_env)
    r = g.post("/api/portal/guardian-authorities", json={"dependant_person_id": person,
                                                        "scopes": ["consent_management", "assigned_tasks"]})
    assert r.json()["status"] == "PENDING_EVIDENCE"
    aid = r.json()["authority_id"]
    rev = reviewer(app, integration_env)
    # Without evidence the reviewer cannot verify and the request is not in the queue.
    assert aid not in [x["authority_id"] for x in rev.get("/api/portal/review/authorities").json()]
    assert rev.post(f"/api/portal/review/authorities/{aid}/decision",
                    json={"decision": "VERIFIED", "reason": "Too early", "valid_days": 30}).status_code == 409
    assert g.post(f"/api/portal/guardian-authorities/{aid}/evidence",
                  json={"evidence_reference": "Fictional court order ref FX-2"}).status_code == 200
    queue = {x["authority_id"]: x for x in rev.get("/api/portal/review/authorities").json()}
    assert queue[aid]["scopes"] == ["assigned_tasks", "consent_management"]
    assert queue[aid]["evidence_reference"] == "Fictional court order ref FX-2"
    # Dependant visible to the guardian as pending; not yet in RLS person projection beyond own creation.
    dep = g.get("/api/portal/dependants").json()
    assert dep[0]["authority"]["status"] == "PENDING_VERIFICATION"
    assert rev.post(f"/api/portal/review/authorities/{aid}/decision",
                    json={"decision": "VERIFIED", "reason": "Fictional document checked", "valid_days": 30}).status_code == 200
    dep = g.get("/api/portal/dependants").json()
    assert dep[0]["authority"]["status"] == "VERIFIED" and dep[0]["authority"]["expires_at"]
    profile = g.get("/api/portal/me").json()
    assert [a["status"] for a in profile["guardian_authorities"]] == ["VERIFIED"]
    # Decided once only.
    assert rev.post(f"/api/portal/review/authorities/{aid}/decision",
                    json={"decision": "REJECTED", "reason": "Changing my mind", "valid_days": None}).status_code == 409


def test_other_guardian_cannot_claim_dependant_by_id_or_name(app, integration_env):
    g1, person = guardian_with_dependant(app, integration_env, name="Same Name Child")
    g2, _ = verified_login(app, integration_env, role="guardian")
    for target in (person, 99999999):
        r = g2.post("/api/portal/guardian-authorities", json={"dependant_person_id": target,
                    "scopes": ["assigned_tasks"], "evidence_reference": "Fictional claim"})
        assert r.status_code == 404  # existing-foreign and nonexistent are indistinguishable
    g2.post("/api/portal/dependants", json={"display_name": "Same Name Child"})
    assert all(d["authority"] is None for d in g2.get("/api/portal/dependants").json())
    assert person not in [d["person_id"] for d in g2.get("/api/portal/dependants").json()]


def test_only_reviewers_can_decide_and_never_their_own(app, integration_env):
    g, person = guardian_with_dependant(app, integration_env)
    aid = g.post("/api/portal/guardian-authorities", json={"dependant_person_id": person,
                 "scopes": ["assigned_tasks"], "evidence_reference": "Fictional ref"}).json()["authority_id"]
    athlete, _ = verified_login(app, integration_env, role="athlete")
    for actor in (g, athlete):
        assert actor.get("/api/portal/review/authorities").status_code == 403
        assert actor.post(f"/api/portal/review/authorities/{aid}/decision",
                          json={"decision": "VERIFIED", "reason": "Self approval", "valid_days": 30}).status_code == 403
    # A guardian who is also a reviewer cannot verify their own request.
    from tests_integration.test_portal_auth import outbox  # noqa: F401
    me = g.get("/api/portal/auth/me").json()
    grant_reviewer(EPHEMERAL_TARGET.admin_url(), me["email"])
    assert aid not in [x["authority_id"] for x in g.get("/api/portal/review/authorities").json()]
    assert g.post(f"/api/portal/review/authorities/{aid}/decision",
                  json={"decision": "VERIFIED", "reason": "Self approval", "valid_days": 30}).status_code == 409


def test_duplicate_open_request_rejected_and_invalid_scope(app, integration_env):
    g, person = guardian_with_dependant(app, integration_env)
    body = {"dependant_person_id": person, "scopes": ["assigned_tasks"]}
    assert g.post("/api/portal/guardian-authorities", json=body).status_code == 201
    assert g.post("/api/portal/guardian-authorities", json=body).status_code == 409
    assert g.post("/api/portal/guardian-authorities",
                  json={**body, "scopes": ["case_notes"]}).status_code == 422
    athlete, _ = verified_login(app, integration_env, role="athlete")
    assert athlete.post("/api/portal/dependants", json={"display_name": "Not allowed"}).status_code == 403


def test_athlete_consent_grant_withdraw_and_history(app, integration_env):
    a, _ = verified_login(app, integration_env, role="athlete")
    me = a.get("/api/portal/auth/me").json()
    person = me["person_id"]
    base = {"subject_person_id": person, "purpose": "CASE_LINK", "practitioner_id": 4242}
    assert a.post("/api/portal/consents", json={**base, "decision": "WITHDRAWN"}).status_code == 422
    first = a.post("/api/portal/consents", json={**base, "decision": "GRANTED"}).json()["event_id"]
    assert a.post("/api/portal/consents", json={**base, "decision": "GRANTED"}).json()["event_id"] == first
    assert a.post("/api/portal/consents", json={**base, "decision": "WITHDRAWN"}).status_code == 201
    assert a.post("/api/portal/consents", json={**base, "practitioner_id": None,
                                                 "decision": "GRANTED"}).status_code == 422
    assert a.post("/api/portal/consents", json={"subject_person_id": person, "purpose": "PLATFORM_TERMS",
                                                 "decision": "GRANTED"}).status_code == 201
    history = a.get(f"/api/portal/consents/{person}").json()
    case = [h for h in history if h["purpose"] == "CASE_LINK"]
    assert [h["decision"] for h in case] == ["GRANTED", "WITHDRAWN"]
    assert [h["current"] for h in case] == [False, True]
    # Another person's ledger is not readable or writable.
    other, _ = verified_login(app, integration_env, role="athlete")
    assert other.get(f"/api/portal/consents/{person}").status_code == 404
    assert other.post("/api/portal/consents", json={**base, "decision": "GRANTED"}).status_code == 404


def test_guardian_proxy_consent_requires_verified_consent_scope(app, integration_env):
    g, person = guardian_with_dependant(app, integration_env)
    body = {"subject_person_id": person, "purpose": "CASE_LINK", "practitioner_id": 77, "decision": "GRANTED"}
    assert g.post("/api/portal/consents", json=body).status_code == 404  # no verified authority yet
    g2, person2, aid, _ = verified_authority(app, integration_env, scopes=["assigned_tasks"])
    assert g2.post("/api/portal/consents", json={**body, "subject_person_id": person2}).status_code == 404
    g3, person3, aid3, rev = verified_authority(app, integration_env)
    r = g3.post("/api/portal/consents", json={**body, "subject_person_id": person3})
    assert r.status_code == 201
    history = g3.get(f"/api/portal/consents/{person3}").json()
    assert history[-1]["by_guardian"] is True


def test_revoked_authority_immediately_blocks_proxy_actions(app, integration_env):
    g, person, aid, rev = verified_authority(app, integration_env)
    body = {"subject_person_id": person, "purpose": "APPOINTMENT_BOOKING", "practitioner_id": 5, "decision": "GRANTED"}
    assert g.post("/api/portal/consents", json=body).status_code == 201
    assert rev.post(f"/api/portal/review/authorities/{aid}/revoke",
                    json={"reason": "Fictional court order changed"}).status_code == 200
    assert g.post("/api/portal/consents", json={**body, "decision": "WITHDRAWN"}).status_code == 404
    assert g.get(f"/api/portal/consents/{person}").status_code == 404
    assert g.get("/api/portal/dependants").json()[0]["authority"]["status"] == "REVOKED"
    # Guardian can request again (renewal) after revocation.
    assert g.post("/api/portal/guardian-authorities", json={"dependant_person_id": person,
                  "scopes": ["assigned_tasks"]}).status_code == 201


def test_guardian_self_revocation(app, integration_env):
    g, person, aid, _ = verified_authority(app, integration_env)
    assert g.post(f"/api/portal/guardian-authorities/{aid}/revoke").status_code == 200
    assert g.post(f"/api/portal/guardian-authorities/{aid}/revoke").status_code == 404


def test_expired_authority_denies_and_can_be_renewed(app, integration_env):
    g, person, aid, _ = verified_authority(app, integration_env)
    engine = create_engine(EPHEMERAL_TARGET.admin_url())
    with engine.begin() as conn:
        conn.execute(text("UPDATE spps_identity_dev.guardian_authorities SET expires_at=now()-interval '1 minute' "
                          "WHERE id=:i"), {"i": aid})
    engine.dispose()
    body = {"subject_person_id": person, "purpose": "CASE_LINK", "practitioner_id": 9, "decision": "GRANTED"}
    assert g.post("/api/portal/consents", json=body).status_code == 404
    assert g.post("/api/portal/guardian-authorities", json={"dependant_person_id": person,
                  "scopes": ["assigned_tasks"]}).status_code == 201


def test_reviewer_has_no_clinical_or_practitioner_access(app, integration_env):
    rev = reviewer(app, integration_env)
    token = rev.cookies.get("spps_portal_session")
    with client(app) as probe:
        probe.cookies.set("modelc_session", token)
        for path in ("/api/athletes", "/api/dashboard", "/api/enquiries", "/api/auth/me"):
            assert probe.get(path).status_code == 401


def test_audit_trail_written_and_append_only(app, integration_env):
    verified_authority(app, integration_env)
    engine = create_engine(EPHEMERAL_TARGET.admin_url())
    try:
        with engine.begin() as conn:
            actions = set(conn.scalars(text("SELECT action FROM spps_identity_dev.platform_audit_events")))
        assert {"dependant.created", "guardian_authority.requested", "guardian_authority.verified",
                "role.reviewer_granted_by_operator"} <= actions
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM spps_identity_dev.platform_audit_events"))
    finally:
        engine.dispose()
