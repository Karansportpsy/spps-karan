"""Stage 13A.7: practitioner–athlete linking across clinical and identity databases.

Permission-matrix cases 3, 4, 9, 10, 11, 14, 16. Fictional data, ephemeral PostgreSQL.
"""
from uuid import uuid4

from tests_integration.test_portal_auth import client, verified_login
from tests_integration.test_portal_guardian import verified_authority


def practitioner(app, env, name="alice"):
    c = client(app)
    r = c.post("/api/auth/login", json={"email": f"{name}@spps.test",
               "password": env[f"SPPS_DEV_{name.upper()}_PASSWORD"], "terms_accepted": True})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def athlete_record(p):
    r = p.post("/api/athletes", json={"code": "LNK-" + uuid4().hex[:16], "display_name": "Fictional Linked Athlete",
                                      "sport": "Hockey", "service_consent_recorded": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def invite(p, record):
    r = p.post(f"/api/athletes/{record}/portal-invitations")
    assert r.status_code == 201, r.text
    return r.json()["code"]


def consent(portal, person, practitioner_id, decision="GRANTED"):
    r = portal.post("/api/portal/consents", json={"subject_person_id": person, "purpose": "CASE_LINK",
                                                  "practitioner_id": practitioner_id, "decision": decision})
    assert r.status_code == 201, r.text


def athlete_portal(app, env):
    a, _ = verified_login(app, env, role="athlete")
    return a, a.get("/api/portal/auth/me").json()["person_id"]


def practitioner_id(p):
    return p.get("/api/auth/me").json()["id"]


def linked(app, env):
    alice = practitioner(app, env)
    record = athlete_record(alice)
    code = invite(alice, record)
    a, person = athlete_portal(app, env)
    consent(a, person, practitioner_id(alice))
    link = a.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person}).json()["link_id"]
    assert alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"}).json()["status"] == "ACTIVE"
    return alice, record, a, person, link


def test_invite_consent_redeem_approve_lifecycle(app, integration_env):
    alice = practitioner(app, integration_env)
    record = athlete_record(alice)
    code = invite(alice, record)
    assert len(code) == 14 and code.count("-") == 2
    assert alice.get(f"/api/athletes/{record}/portal-links").json()[0]["status"] == "INVITED"
    a, person = athlete_portal(app, integration_env)
    # Consent is required before redeeming.
    r = a.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person})
    assert r.status_code == 409
    consent(a, person, practitioner_id(alice))
    r = a.post("/api/portal/links/redeem", json={"code": code.lower().replace("-", ""), "subject_person_id": person})
    assert r.status_code == 201 and r.json()["status"] == "PRACTITIONER_REVIEW"
    link = r.json()["link_id"]
    # Not active until the practitioner approves (matrix case 3).
    mine = a.get("/api/portal/links").json()
    assert mine[0]["status"] == "PRACTITIONER_REVIEW" and mine[0]["active"] is False
    assert a.get("/api/portal/me").json()["practitioner_links"] == []  # RLS projection: active only
    # Code is single-use.
    assert a.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person}).status_code == 400
    pending = [x for x in alice.get("/api/portal-links").json() if x["link_id"] == link][0]
    assert pending["requester_display_name"] == "Fictional Portal User" and pending["consent_current"] is True
    assert alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"}).json()["status"] == "ACTIVE"
    assert a.get("/api/portal/links").json()[0]["active"] is True
    assert [l["id"] for l in a.get("/api/portal/me").json()["practitioner_links"]] == [link]


def test_other_practitioner_cannot_invite_or_decide(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    bob = practitioner(app, integration_env, "bob")
    # Matrix case 4 and 14: foreign record and forged link IDs.
    assert bob.post(f"/api/athletes/{record}/portal-invitations").status_code == 404
    assert bob.get(f"/api/athletes/{record}/portal-links").status_code == 404
    for decision in ("approve", "decline", "revoke"):
        assert bob.post(f"/api/portal-links/{link}/decision", json={"decision": decision}).status_code == 404
    assert link not in [x["link_id"] for x in bob.get("/api/portal-links").json()]
    assert a.get("/api/portal/links").json()[0]["active"] is True


def test_one_person_two_practitioners_stay_isolated(app, integration_env):
    alice, record_a, a, person, link_a = linked(app, integration_env)
    bob = practitioner(app, integration_env, "bob")
    record_b = athlete_record(bob)
    code_b = invite(bob, record_b)
    # Alice's consent does not cover Bob (matrix case 10).
    assert a.post("/api/portal/links/redeem", json={"code": code_b, "subject_person_id": person}).status_code == 409
    consent(a, person, practitioner_id(bob))
    link_b = a.post("/api/portal/links/redeem", json={"code": code_b, "subject_person_id": person}).json()["link_id"]
    bob.post(f"/api/portal-links/{link_b}/decision", json={"decision": "approve"})
    assert {l["link_id"] for l in a.get("/api/portal/links").json()} == {link_a, link_b}
    # Matrix case 9: each practitioner sees only their own link and record.
    assert {x["link_id"] for x in alice.get("/api/portal-links").json() if x["link_id"]} >= {link_a}
    assert link_b not in [x["link_id"] for x in alice.get("/api/portal-links").json()]
    assert link_a not in [x["link_id"] for x in bob.get("/api/portal-links").json()]
    assert alice.get(f"/api/athletes/{record_b}").status_code == 404
    assert bob.get(f"/api/athletes/{record_a}").status_code == 404


def test_revocation_by_subject_and_practitioner_is_immediate(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    assert a.post(f"/api/portal/links/{link}/revoke").status_code == 200
    assert a.get("/api/portal/links").json()[0]["active"] is False
    assert a.get("/api/portal/me").json()["practitioner_links"] == []
    assert alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"}).json()["status"] == "REVOKED"
    # Re-link is possible with a fresh invitation.
    code = invite(alice, record)
    relink = a.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person})
    assert relink.status_code == 201
    new = relink.json()["link_id"]
    alice.post(f"/api/portal-links/{new}/decision", json={"decision": "approve"})
    assert alice.post(f"/api/portal-links/{new}/decision", json={"decision": "revoke",
                      "reason": "Fictional end of service"}).json()["status"] == "REVOKED"
    assert all(l["active"] is False for l in a.get("/api/portal/links").json())


def test_consent_withdrawal_deactivates_active_link(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    consent(a, person, practitioner_id(alice), "WITHDRAWN")
    assert a.get("/api/portal/links").json()[0]["active"] is False
    assert a.get("/api/portal/me").json()["practitioner_links"] == []
    assert [x for x in alice.get("/api/portal-links").json() if x["link_id"] == link][0]["consent_current"] is False


def test_approval_rechecks_consent(app, integration_env):
    alice = practitioner(app, integration_env)
    record = athlete_record(alice)
    code = invite(alice, record)
    a, person = athlete_portal(app, integration_env)
    consent(a, person, practitioner_id(alice))
    link = a.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person}).json()["link_id"]
    consent(a, person, practitioner_id(alice), "WITHDRAWN")
    assert alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"}).json()["status"] == "CONSENT_PENDING"


def test_guardian_links_dependant_and_loses_access_on_authority_revocation(app, integration_env):
    g, dependant, aid, rev = verified_authority(app, integration_env)
    alice = practitioner(app, integration_env)
    record = athlete_record(alice)
    code = invite(alice, record)
    consent(g, dependant, practitioner_id(alice))
    r = g.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": dependant})
    assert r.status_code == 201
    link = r.json()["link_id"]
    view = [x for x in alice.get("/api/portal-links").json() if x["link_id"] == link][0]
    assert view["via_guardian"] is True and view["subject_display_name"] == "Fictional Dependant"
    assert alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"}).json()["status"] == "ACTIVE"
    assert g.get("/api/portal/links").json()[0]["active"] is True
    rev.post(f"/api/portal/review/authorities/{aid}/revoke", json={"reason": "Fictional authority ended"})
    assert g.get("/api/portal/links").json() == []
    assert g.post(f"/api/portal/links/{link}/revoke").status_code == 404


def test_unrelated_accounts_cannot_redeem_for_others(app, integration_env):
    alice = practitioner(app, integration_env)
    code = invite(alice, athlete_record(alice))
    a, person = athlete_portal(app, integration_env)
    other, other_person = athlete_portal(app, integration_env)
    # Cannot redeem on behalf of a different person (also hides existence).
    assert other.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person}).status_code == 404
    # Guessing codes is throttled per account.
    statuses = [other.post("/api/portal/links/redeem", json={"code": "AAAA-BBBB-CCCC",
                           "subject_person_id": other_person}).status_code for _ in range(11)]
    assert statuses[0] in (400, 409) and statuses[-1] == 429


def test_new_invitation_supersedes_previous_and_anonymous_contact_cannot_link(app, integration_env):
    alice = practitioner(app, integration_env)
    record = athlete_record(alice)
    first, second = invite(alice, record), invite(alice, record)
    a, person = athlete_portal(app, integration_env)
    consent(a, person, practitioner_id(alice))
    assert a.post("/api/portal/links/redeem", json={"code": first, "subject_person_id": person}).status_code == 400
    assert a.post("/api/portal/links/redeem", json={"code": second, "subject_person_id": person}).status_code == 201
    # Matrix case 16: public enquiries/bookings never create portal links.
    with client(app) as anon:
        assert anon.post("/api/portal/links/redeem", json={"code": second, "subject_person_id": person}).status_code == 401
