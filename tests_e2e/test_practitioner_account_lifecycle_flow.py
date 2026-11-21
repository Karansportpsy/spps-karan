"""Account lifecycle phase A against real PostgreSQL roles (migration 0032, SPPS_ACCOUNT_LIFECYCLE_ENABLED=true).

Proves what the SQLite tests cannot: the SECURITY DEFINER switch, the directory/photo functions, that the
application role gained no table right, row-level isolation of the export under the real roles (no table may be
refused: `warnings` must be empty) and the startup refusal. Self-contained: it finds or claims the owner itself, so
it runs after the owner/registration story or alone.
"""
import json
import time

import psycopg
import pytest

pytestmark = pytest.mark.e2e

STATE = {}


def _owner(w):
    with w.h.admin_conn() as c:
        row = c.execute("SELECT pr.supabase_auth_uid FROM spps_dev.practitioners p "
                        "JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id WHERE p.is_admin").fetchone()
    uid = str(row[0]) if row and row[0] else w.g.signup(w.owner_email)
    browser = w.h.Browser(w.app, w.g)
    assert browser.sign_in(uid).status_code == 200
    return browser


def _listed(w):
    return {row["id"] for row in w.visitor.get("/api/public/practitioners").json()}


def _state(browser):
    r = browser.get("/api/account/lifecycle")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_listed_practitioner_exists(world):
    w = world
    owner = STATE["owner"] = _owner(w)
    uid = w.g.signup("lifecycle.tester@example.com")
    b = w.h.Browser(w.app, w.g)
    r = b.post("/api/public/practitioner-registration", json={
        "name": "Lifecycle Tester", "email": "lifecycle.tester@example.com", "supabase_access_token": w.g.token(uid),
        "qualifications": "MSc Sport Psychology, BASES accredited", "experience_years": 5,
        "achievements": "Fictional achievements", "specialisms": "Focus, confidence",
        "consultation_fee_paise": 0, "plan": "free", "photo_base64": w.h.tiny_photo_b64(), "terms_accepted": True})
    assert r.status_code == 201, r.text
    queue = owner.get("/api/admin/practitioner-reviews").json()
    row = next(x for x in queue if x["name"] == "Lifecycle Tester")
    pid = STATE["pid"] = row["practitioner_id"]
    approved = owner.send("POST", f"/api/admin/practitioners/{pid}/approval",
                          json={"decision": "approve", "note": "Fictional credentials checked"})
    assert approved.status_code == 200, approved.text
    assert b.sign_in(uid).status_code == 200
    STATE.update(uid=uid, me=b)
    assert pid in _listed(w)
    state = _state(b)
    assert state["deactivated"] is False and state["listed"] is True and state["reauth"] == "supabase"


def test_deactivation_removes_the_public_surface_but_not_the_workspace(world):
    w, me, pid = world, STATE["me"], STATE["pid"]
    off = me.send("POST", "/api/account/deactivate", json={"confirm": True})
    assert off.status_code == 200, off.text
    assert off.json()["deactivated"] is True and off.json()["listed"] is False
    assert pid not in _listed(w), "public_practitioner_directory must drop the practitioner"
    assert w.visitor.get(f"/api/public/practitioners/{pid}/photo").status_code == 404
    assert me.get("/api/athletes").status_code == 200
    assert me.get("/api/auth/me").status_code == 200
    with w.h.admin_conn() as c:
        status = c.execute("SELECT status, credentials_verified, public_approved, deactivated_at IS NOT NULL "
                           "FROM spps_dev.practitioner_profiles WHERE practitioner_id=%s", (pid,)).fetchone()
    assert status == ("approved", True, True, True), "no review flag is touched"
    sign_in_again = w.h.Browser(w.app, w.g).sign_in(STATE["uid"])
    assert sign_in_again.status_code == 200, "a deactivated practitioner can still sign in"


def test_the_application_role_gained_no_table_right_and_the_function_is_self_scoped(world):
    w, pid = world, STATE["pid"]
    with w.h.role_conn("spps_dev_app") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE spps_dev.practitioner_profiles SET deactivated_at = NULL")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT spps_dev.set_own_account_deactivation(false)")  # no identity in this transaction
        c.rollback()
    with w.h.role_conn("spps_dev_auth") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT spps_dev.set_own_account_deactivation(false)")
    with w.h.admin_conn() as c:
        public_exec = c.execute("SELECT has_function_privilege('public', 'spps_dev.set_own_account_deactivation(boolean)', 'EXECUTE')").fetchone()[0]
        owner_stamp = c.execute("SELECT count(*) FROM spps_dev.practitioner_profiles WHERE deactivated_at IS NOT NULL").fetchone()[0]
    assert public_exec is False
    assert owner_stamp == 1, "only the one practitioner who asked is deactivated"


def test_the_function_only_ever_changes_the_callers_own_profile(world):
    w, pid = world, STATE["pid"]
    with w.h.admin_conn() as c:
        tenant_id = c.execute("SELECT tenant_id FROM spps_dev.practitioners WHERE id=%s", (pid,)).fetchone()[0]
        owner_id = c.execute("SELECT id FROM spps_dev.practitioners WHERE is_admin").fetchone()[0]
    with w.h.role_conn("spps_dev_app") as c:
        c.execute("SELECT set_config('spps.practitioner_id', %s, true), set_config('spps.tenant_id', %s, true)",
                  (str(owner_id), str(tenant_id)))
        stamp = c.execute("SELECT spps_dev.set_own_account_deactivation(true)").fetchone()[0]
        assert stamp is not None
        c.commit()
    with w.h.admin_conn() as c:
        rows = dict(c.execute("SELECT practitioner_id, deactivated_at IS NOT NULL FROM spps_dev.practitioner_profiles "
                              "WHERE practitioner_id IN (%s,%s)", (owner_id, pid)).fetchall())
    assert rows == {owner_id: True, pid: True}
    with w.h.role_conn("spps_dev_app") as c:
        c.execute("SELECT set_config('spps.practitioner_id', %s, true), set_config('spps.tenant_id', %s, true)",
                  (str(owner_id), str(tenant_id)))
        assert c.execute("SELECT spps_dev.set_own_account_deactivation(false)").fetchone()[0] is None
        c.commit()
    with w.h.admin_conn() as c:
        rows = dict(c.execute("SELECT practitioner_id, deactivated_at IS NOT NULL FROM spps_dev.practitioner_profiles "
                              "WHERE practitioner_id IN (%s,%s)", (owner_id, pid)).fetchall())
    assert rows == {owner_id: False, pid: True}, "acting as the owner never touched the other practitioner"


def test_reactivation_restores_the_listing_without_a_new_review(world):
    w, me, pid = world, STATE["me"], STATE["pid"]
    on = me.send("POST", "/api/account/reactivate", json={"confirm": True})
    assert on.status_code == 200 and on.json()["listed"] is True and on.json()["deactivated"] is False
    assert pid in _listed(w)
    assert w.visitor.get(f"/api/public/practitioners/{pid}/photo").status_code == 200


def test_export_reads_every_table_under_the_real_roles_and_only_own_rows(world):
    w, me, owner, uid = world, STATE["me"], STATE["owner"], STATE["uid"]
    mine = me.send("POST", "/api/athletes", json={"code": "LC-001", "display_name": "Lifecycle Client", "sport": "Archery",
                                                  "service_consent_recorded": True})
    assert mine.status_code in (200, 201), mine.text
    theirs = owner.send("POST", "/api/athletes", json={"code": "OWN-LC", "display_name": "Owner Only Client", "sport": "Cricket",
                                                    "service_consent_recorded": True})
    assert theirs.status_code in (200, 201), theirs.text
    case = me.send("POST", "/api/cases", json={"athlete_id": mine.json()["id"], "title": "Fictional case",
                                                "category": "Performance psychology", "goal": "Routine", "next_review": None})
    assert case.status_code in (200, 201), case.text

    result = me.send("POST", "/api/account/export", json={"supabase_access_token": w.g.token(uid)})
    assert result.status_code == 200, result.text
    document = result.json()
    assert document["warnings"] == [], f"a table was refused to the application role: {document['warnings']}"
    data = document["data"]
    assert [a["code"] for a in data["athletes"]] == ["LC-001"]
    assert [c["title"] for c in data["cases"]] == ["Fictional case"]
    assert "Owner Only Client" not in result.text and "OWN-LC" not in result.text
    assert data["profile"]["practitioner_id"] == STATE["pid"]
    for secret in ("password_hash", "token_hash", "csrf_token", "avatar_bytes", "supabase_auth_uid"):
        assert secret not in result.text, secret
    assert result.headers["cache-control"] == "no-store"
    assert "account.exported" in _audit(w, STATE["pid"])


def _audit(w, pid):
    with w.h.admin_conn() as c:
        return [r[0] for r in c.execute("SELECT action FROM spps_dev.audit_events WHERE object_type='practitioner' "
                                        "AND object_id=%s ORDER BY id", (pid,)).fetchall()]


def test_export_needs_a_fresh_password_sign_in_of_the_same_account(world):
    w, me, uid = world, STATE["me"], STATE["uid"]
    send = lambda token: me.send("POST", "/api/account/export", json={"supabase_access_token": token})
    assert me.send("POST", "/api/account/export", json={}).status_code == 401
    assert send(w.g.token(uid, signed_at=int(time.time()) - 3600)).status_code == 401, "an hour-old sign-in is stale"
    assert send(w.g.token(uid, password=False)).status_code == 403, "an e-mail link is not a password confirmation"
    other = w.g.signup("someone.else@example.com")
    assert send(w.g.token(other)).status_code == 403, "another account's fresh sign-in does not count"
    assert me.send("POST", "/api/account/export", json={"password": "anything"}).status_code == 401
    assert send(w.g.token(uid)).status_code == 200


def test_unauthenticated_and_cross_session_use_is_refused(world):
    w = world
    assert w.visitor.get("/api/account/lifecycle").status_code == 401
    assert w.visitor.post("/api/account/export", json={}).status_code in (401, 403)
    assert w.visitor.post("/api/account/deactivate", json={"confirm": True}).status_code in (401, 403)
    me = STATE["me"]
    assert w.h.Browser(w.app, w.g).c.post("/api/account/deactivate", json={"confirm": True},
                                         headers={"X-CSRF-Token": me.csrf}).status_code in (401, 403)


def test_startup_refuses_the_flag_when_the_database_lacks_migration_0032(world):
    w = world
    from server.release_preflight import verify_production
    verify_production(w.app)  # the fully migrated database passes with the flag on
    with w.h.admin_conn() as c:
        c.execute("ALTER TABLE spps_dev.practitioner_profiles RENAME COLUMN deactivated_at TO deactivated_at_off")
    try:
        with pytest.raises(RuntimeError, match="0032"):
            verify_production(w.app)
    finally:
        with w.h.admin_conn() as c:
            c.execute("ALTER TABLE spps_dev.practitioner_profiles RENAME COLUMN deactivated_at_off TO deactivated_at")
    verify_production(w.app)
    with w.h.admin_conn() as c:
        c.execute("REVOKE EXECUTE ON FUNCTION spps_dev.set_own_account_deactivation(boolean) FROM spps_dev_app")
    try:
        with pytest.raises(RuntimeError, match="0032"):
            verify_production(w.app)
    finally:
        with w.h.admin_conn() as c:
            c.execute("GRANT EXECUTE ON FUNCTION spps_dev.set_own_account_deactivation(boolean) TO spps_dev_app")
    verify_production(w.app)
