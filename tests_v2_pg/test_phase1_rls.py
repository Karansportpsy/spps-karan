"""Phase 1 on PostgreSQL: pending applications are invisible to practitioners; the owner sees its tenant only."""
from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient

from .conftest import as_role, denied, pg_login, q


def _claim_hash(claim):
    return hashlib.sha256(("spps-claim:" + claim).encode()).hexdigest()


def _submit(conn, email, claim):
    return q(conn, """SELECT spps_dev.v2_submit_pending_application('winmindperform',%(e)s,%(c)s,'Dr Fictional Pg',
                      'Fictional MSc Sport Psychology',4,'','')""", {"e": email, "c": _claim_hash(claim)})[0][0]


def test_server_startup_role_check_counts_v2_tables_as_force_rls(pgenv):
    from sqlalchemy import create_engine
    from server.identity import check_role
    engine = create_engine(pgenv["SPPS_V2_PG_SPPS_DEV_APP_URL"], connect_args={"options": "-c search_path=spps_dev"})
    try:
        check_role(engine, "spps_dev_app", require_clinical=True)  # raises if any table is not FORCE RLS
    finally:
        engine.dispose()


def test_anonymous_can_submit_but_never_read_pending_applications(pgenv):
    with as_role(pgenv, "spps_dev_app") as anon:  # no identity set: an anonymous public request
        app_id = _submit(anon, "pg.anon@example.org", uuid.uuid4().hex * 2)
        assert app_id > 0
        assert q(anon, "SELECT count(*) FROM registration_applications")[0][0] == 0
        assert denied(anon, """INSERT INTO registration_applications(tenant_id,email,state,expires_at)
                               VALUES (1,'x@example.org','approved',now())""")


def test_practitioner_cannot_read_another_applicants_pending_application(pgenv, people, superuser):
    with as_role(pgenv, "spps_dev_app") as anon:
        _submit(anon, "pg.victim@example.org", uuid.uuid4().hex * 2)
        anon.commit()
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert q(a, "SELECT count(*) FROM registration_applications")[0][0] == 0
        assert q(a, "SELECT count(*) FROM registration_transitions")[0][0] == 0
        assert q(a, "SELECT count(*) FROM email_deliveries")[0][0] == 0
        with a.cursor() as cur:
            cur.execute("UPDATE registration_applications SET state='approved'")
            assert cur.rowcount == 0
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert q(owner, "SELECT count(*) FROM registration_applications WHERE email='pg.victim@example.org'")[0][0] >= 1
    with as_role(pgenv, "spps_dev_auth", people["a"]) as auth:
        assert denied(auth, "SELECT * FROM registration_applications")


def test_owner_cannot_forge_transitions_or_reach_another_tenant(pgenv, people, superuser):
    other_tenant = q(superuser, "INSERT INTO spps_dev.tenants(slug,display_name) VALUES (%(s)s,'Other') RETURNING id",
                     {"s": "other-" + uuid.uuid4().hex[:6]})[0][0]
    q(superuser, """INSERT INTO spps_dev.registration_applications(tenant_id,email,state,has_application,expires_at)
                    VALUES (%(t)s,'other.tenant@example.org','applied',true,now()+interval '1 day')""",
      {"t": other_tenant})
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert q(owner, "SELECT count(*) FROM registration_applications WHERE tenant_id=%(t)s",
                 {"t": other_tenant})[0][0] == 0
        app_id = q(owner, "SELECT id FROM registration_applications LIMIT 1")[0][0]
        assert denied(owner, """INSERT INTO registration_transitions(application_id,tenant_id,from_state,to_state,
                                actor_kind,actor_practitioner_id) VALUES (%(a)s,%(t)s,'under_review','approved',
                                'owner',%(p)s)""", {"a": app_id, "t": people["owner"]["tenant_id"],
                                                     "p": people["a"]["id"]})
        # Unverified rows cannot be moved into review by the owner either.
        with owner.cursor() as cur:
            cur.execute("UPDATE registration_applications SET state='under_review' WHERE state='applied'")
            assert cur.rowcount == 0


def test_promotion_function_requires_matching_claim_and_email(pgenv, superuser):
    email, claim = f"pg.{uuid.uuid4().hex[:6]}@example.org", uuid.uuid4().hex * 2
    uid = str(uuid.uuid4())
    with as_role(pgenv, "spps_dev_app") as anon:
        _submit(anon, email, claim)
        # Wrong e-mail with the right claim: recorded as a confirmed account without application.
        res = q(anon, """SELECT * FROM spps_dev.v2_promote_application(%(u)s::uuid,'thief@example.org',
                         'winmindperform',%(c)s,NULL,NULL,NULL,NULL,NULL,%(h)s)""",
                {"u": str(uuid.uuid4()), "c": _claim_hash(claim), "h": "x" * 40})
        assert res[0][:3] == ("email_verified", None, False)
        res = q(anon, """SELECT * FROM spps_dev.v2_promote_application(%(u)s::uuid,%(e)s,'winmindperform',%(c)s,
                         NULL,NULL,NULL,NULL,NULL,%(h)s)""", {"u": uid, "e": email, "c": _claim_hash(claim), "h": "x" * 40})
        assert res[0][0] == "under_review" and res[0][1] and res[0][3] is True
        anon.commit()
    status = q(superuser, """SELECT pr.status, p.active, p.is_admin FROM spps_dev.practitioner_profiles pr
                             JOIN spps_dev.practitioners p ON p.id=pr.practitioner_id WHERE p.email=%(e)s""", {"e": email})
    assert status == [("pending_approval", False, False)]
    moves = q(superuser, """SELECT from_state,to_state,actor_kind FROM spps_dev.registration_transitions t
                            JOIN spps_dev.registration_applications r ON r.id=t.application_id
                            WHERE r.email=%(e)s ORDER BY t.id""", {"e": email})
    assert moves == [("applied", "email_verified", "system"), ("email_verified", "under_review", "system")]


def test_full_flow_over_rls_webview_signup_chrome_confirmation(pg_app, fake_supabase, people):
    email = f"pg.flow.{uuid.uuid4().hex[:6]}@example.org"
    webview = TestClient(pg_app)
    tok = webview.get("/api/public/form-token").json()["form_token"]
    res = webview.post("/api/public/practitioner-applications/pending", json={
        "email": email, "name": "Dr Fictional Flow", "qualifications": "Fictional MSc Sport Psychology",
        "experience_years": 3, "achievements": "", "specialisms": "", "terms_accepted": True, "form_token": tok})
    assert res.status_code == 201, res.text
    account = fake_supabase.account(email, confirmed=True, metadata={"spps_claim": res.json()["claim"]})
    chrome = TestClient(pg_app)
    res = chrome.post("/api/public/practitioner-application/promote", json={"access_token": account})
    assert res.status_code == 200 and res.json()["state"] == "under_review", res.text

    owner = TestClient(pg_app)
    h = pg_login(owner, people["owner"])
    summary = owner.get("/api/admin/registrations/summary")
    assert summary.status_code == 200 and summary.json()["new_applications"] >= 1
    pid = res.json()["practitioner_id"]
    queue = owner.get("/api/admin/practitioner-reviews").json()
    assert any(r["practitioner_id"] == pid and r["qualifications"] == "Fictional MSc Sport Psychology" for r in queue)
    decision = owner.post(f"/api/admin/practitioners/{pid}/approval",
                          json={"decision": "request_more_info", "note": "Add a registration number"}, headers=h)
    assert decision.status_code == 200, decision.text
    failures = owner.get("/api/admin/registrations/summary").json()["email_failures"]
    assert any(f["practitioner_id"] == pid and f["reason"] == "mail_not_configured" for f in failures)

    other = TestClient(pg_app)
    pg_login(other, people["a"])
    assert other.get("/api/admin/registrations/summary").status_code == 403
