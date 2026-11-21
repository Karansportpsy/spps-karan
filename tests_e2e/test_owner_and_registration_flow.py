"""Assertion-based end-to-end suite for the owner / registration / directory requirements.

Order matters: the module is one story told against one freshly built database (pytest runs it top to bottom).
"""
import pytest

pytestmark = pytest.mark.e2e

STATE = {}


def _register(w, email, name, qualifications="MSc Sport Psychology, BASES accredited"):
    uid = w.g.signup(email)
    b = w.h.Browser(w.app, w.g)
    r = b.post("/api/public/practitioner-registration", json={
        "name": name, "email": email, "supabase_access_token": w.g.token(uid),
        "qualifications": qualifications, "experience_years": 5,
        "achievements": "Worked with county squads", "specialisms": "Focus, confidence",
        "consultation_fee_paise": 0, "plan": "free", "photo_base64": w.h.tiny_photo_b64(), "terms_accepted": True})
    return uid, b, r


def _admins(w):
    with w.h.admin_conn() as c:
        return [r[0] for r in c.execute("SELECT lower(email) FROM spps_dev.practitioners WHERE is_admin")]


def _directory(w):
    r = w.visitor.get("/api/public/practitioners")
    assert r.status_code == 200
    return r.json()


def _queue_row(w, name):
    rows = STATE["owner"].get("/api/admin/practitioner-reviews").json()
    return next(x for x in rows if x["name"] == name)


# ---- 1. database and startup -------------------------------------------------------------------------------------

def test_serial_sql_builds_and_matches_the_expected_shape(world):
    w = world
    assert w.sql_steps == 48  # 00..47: the serial set including 0042 (booking-only), 0043 (services), 0044 (index), 0045 (appointments and video), 0046 (notifications)
    with w.h.admin_conn() as c:
        one = lambda q: c.execute(q).fetchone()[0]
        assert one("SELECT count(*) FROM pg_tables WHERE schemaname='spps_dev'") == 63
        assert one("SELECT count(*) FROM pg_tables WHERE schemaname='spps_identity_dev'") == 40
        assert one("SELECT count(*) FROM pg_policies WHERE schemaname='spps_dev'") == 96
        assert one("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_dev'") == 26
        assert one("SELECT count(*) FROM spps_ops.migration_history") == 45
        assert one("""SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                      JOIN pg_namespace n ON n.oid=c.relnamespace
                      WHERE con.contype='f' AND n.nspname IN ('spps_dev','spps_identity_dev') AND NOT EXISTS (
                        SELECT 1 FROM pg_index i WHERE i.indrelid=con.conrelid
                        AND (i.indkey::int2[])[0:cardinality(con.conkey)-1] @> con.conkey
                        AND (i.indkey::int2[])[0:cardinality(con.conkey)-1] <@ con.conkey)""") == 0, \
            "every foreign key has a covering index (0030/0031)"
        assert one("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                   "WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND c.relkind='r' "
                   "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)") == 0
        assert one("SELECT string_agg(slug, ',') FROM spps_dev.tenants") == "winmindperform"


def test_production_app_starts_and_is_ready(world):
    r = world.visitor.get("/health/ready")
    assert r.status_code == 200, r.text
    assert world.visitor.get("/api/public/supabase-auth-config").json()["enabled"] is True
    assert _directory(world) == [], "nobody is listed before the owner has signed in"


# ---- 2. owner is the default admin --------------------------------------------------------------------------------

def test_owner_email_must_be_confirmed_before_it_is_trusted(world):
    w = world
    uid = w.g.signup(w.owner_email, confirmed=False)
    r = w.h.Browser(w.app, w.g).sign_in(uid)
    assert r.status_code in (401, 403), r.text
    assert _admins(w) == [], "an unconfirmed address must never create the admin"


def test_owner_signs_in_as_admin_with_both_workspaces(world):
    w = world
    STATE["owner_uid"] = w.g.signup(w.owner_email)
    owner = STATE["owner"] = w.h.Browser(w.app, w.g)
    assert owner.sign_in(STATE["owner_uid"]).status_code == 200
    me = owner.get("/api/auth/me").json()
    assert me["is_admin"] is True
    assert _admins(w) == [w.owner_email.lower()]
    # one session reaches the admin dashboard AND the practitioner workspace
    assert owner.get("/api/admin/practitioner-reviews").status_code == 200
    for path in ("/api/dashboard", "/api/athletes", "/api/practitioner/profile"):
        assert owner.get(path).status_code == 200, path


def test_a_different_identity_cannot_take_over_the_owner_address(world):
    w = world
    impostor_uid = w.g.signup(w.owner_email)
    r = w.h.Browser(w.app, w.g).sign_in(impostor_uid)
    assert r.status_code == 403, r.text
    assert _admins(w) == [w.owner_email.lower()]


# ---- 3. owner is listed automatically and stays on top -------------------------------------------------------------

def test_owner_is_listed_automatically_without_registering(world):
    listing = _directory(world)
    assert len(listing) == 1
    assert listing[0]["featured_owner"] is True and listing[0]["credentials_verified"] is True
    assert set(listing[0]).isdisjoint({"email", "password_hash", "review_note", "supabase_auth_uid"})


# ---- 4. controlled registration ----------------------------------------------------------------------------------

def test_new_registration_is_pending_hidden_and_blocked(world):
    w = world
    uid, browser, r = _register(w, "applicant.one@example.com", "Dr Applicant One")
    assert r.status_code in (200, 201), r.text
    STATE.update(a_uid=uid, a=browser)
    assert [p["name"] for p in _directory(w)] == [_directory(w)[0]["name"]], "applicant is not listed while pending"
    blocked = browser.sign_in(uid)
    assert blocked.status_code == 403 and "pending" in blocked.text.lower(), blocked.text
    assert browser.get("/api/auth/me").status_code == 401, "no session may be issued to a pending applicant"
    row = _queue_row(w, "Dr Applicant One")
    assert row["status"] == "pending_approval" and row["email_verified"] is True
    STATE["a_id"] = row["practitioner_id"]


def test_there_is_no_enquiry_surface_for_anyone(world):
    """Booking-only directory (migration 0042): the public form and every inbox are gone, for visitors,
    practitioners and the owner alike. Appointments are requested from the athlete portal and approved by the
    practitioner; a pending applicant is never listed, so nobody can book them."""
    w = world
    # In hosted mode the route allowlist answers 503 for a path that no longer exists (404 elsewhere).
    gone = (404, 405, 503)
    assert w.visitor.post("/api/public/enquiries", json={"contact_name": "Test Athlete", "website": ""}).status_code in gone
    for path in ("/api/enquiries", "/api/v2/directory/enquiries"):
        assert w.visitor.get(path).status_code in gone + (401,), path
    assert STATE["a_id"] not in {p["id"] for p in _directory(w)}, "a pending applicant is not listed"


def test_impostors_cannot_become_admin_at_any_layer(world):
    w = world
    a = STATE["a"]
    # API: no session at all, and a non-admin session, are both refused
    assert w.visitor.get("/api/admin/practitioner-reviews").status_code == 401
    # database: the CHECK constraint refuses is_admin for anyone but the owner, even for the superuser
    import psycopg
    with w.h.admin_conn() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE spps_dev.practitioners SET is_admin = true WHERE id = %s", (STATE["a_id"],))
    # database: the application role cannot flip the flag either (no column privilege, or no visible row)
    with w.h.role_conn("spps_dev_app") as c:
        try:
            c.execute("UPDATE spps_dev.practitioners SET is_admin = true")
        except psycopg.errors.InsufficientPrivilege:
            pass
        c.rollback()
    assert _admins(w) == [w.owner_email.lower()]
    assert a.csrf == ""


# ---- 5. approval workflow ---------------------------------------------------------------------------------------

def test_request_more_info_then_applicant_responds(world):
    w = world
    owner = STATE["owner"]
    b_uid, b, r = _register(w, "applicant.two@example.com", "Dr Applicant Two")
    assert r.status_code in (200, 201)
    b_id = _queue_row(w, "Dr Applicant Two")["practitioner_id"]
    STATE.update(b_uid=b_uid, b=b, b_id=b_id)
    r = owner.send("POST", f"/api/admin/practitioners/{b_id}/approval",
                   json={"decision": "request_more_info", "note": "Please send your accreditation certificate"})
    assert r.status_code == 200 and r.json()["status"] == "pending_approval"
    assert _queue_row(w, "Dr Applicant Two")["phase"] == "more_info"
    status = b.post("/api/public/practitioner-application/status", json={"access_token": w.g.token(b_uid)}).json()
    assert status["phase"] == "more_info" and "accreditation" in status["message"] and status["can_update"] is True
    r = b.post("/api/public/practitioner-application/update", json={
        "access_token": w.g.token(b_uid), "qualifications": "Updated qualifications and certificate link",
        "experience_years": 6, "achievements": "Certificate shared", "specialisms": "Focus"})
    assert r.status_code == 200, r.text
    row = _queue_row(w, "Dr Applicant Two")
    assert row["phase"] == "responded" and row["qualifications"].startswith("Updated")
    assert "Applicant Two" not in [p["name"] for p in _directory(w)]


def test_approval_lists_the_practitioner_below_the_owner(world):
    w = world
    r = STATE["owner"].send("POST", f"/api/admin/practitioners/{STATE['a_id']}/approval",
                            json={"decision": "approve", "note": "Credentials checked"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    signed_in = STATE["a"].sign_in(STATE["a_uid"])
    assert signed_in.status_code == 200 and STATE["a"].get("/api/auth/me").json()["is_admin"] is False
    names = [p["name"] for p in _directory(w)]
    flags = [p["featured_owner"] for p in _directory(w)]
    assert flags == [True, False] and "Dr Applicant One" in names
    # approve a second applicant: the owner must still be first and the order must be stable
    r = STATE["owner"].send("POST", f"/api/admin/practitioners/{STATE['b_id']}/approval", json={"decision": "approve", "note": "Credentials checked"})
    assert r.status_code == 200
    flags = [p["featured_owner"] for p in _directory(w)]
    assert flags == [True, False, False]
    assert _admins(w) == [w.owner_email.lower()]


def test_approved_practitioner_and_owner_have_no_enquiry_inbox(world):
    for who in ("a", "owner"):
        browser = STATE[who]
        assert browser.get("/api/enquiries").status_code in (404, 405, 503), who
        assert "new_enquiries" not in browser.get("/api/dashboard").json().get("stats", {}), who


# ---- 6. profile edits ------------------------------------------------------------------------------------------

def test_owner_profile_edit_keeps_listing_and_sign_in(world):
    w = world
    r = STATE["owner"].send("PUT", "/api/practitioner/profile", json={
        "qualifications": "PhD (Sports Science) · MA (Sport Psychology)", "experience_years": 12,
        "achievements": "Updated achievements", "specialisms": "Injury psychology"})
    assert r.status_code == 200 and r.json()["review_required"] is False
    listing = _directory(w)
    assert listing[0]["featured_owner"] is True and listing[0]["qualifications"].startswith("PhD")
    assert w.h.Browser(w.app, w.g).sign_in(STATE["owner_uid"]).status_code == 200


def test_ordinary_practitioner_profile_edit_returns_to_review(world):
    w = world
    r = STATE["a"].send("PUT", "/api/practitioner/profile", json={
        "qualifications": "MSc Sport Psychology, BASES accredited, new role", "experience_years": 7,
        "achievements": "Updated", "specialisms": "Focus"})
    assert r.status_code == 200 and r.json()["review_required"] is True
    assert "Dr Applicant One" not in [p["name"] for p in _directory(w)]
    r = STATE["owner"].send("POST", f"/api/admin/practitioners/{STATE['a_id']}/approval", json={"decision": "approve", "note": "Credentials checked"})
    assert r.status_code == 200
    assert "Dr Applicant One" in [p["name"] for p in _directory(w)]


# ---- 7. clinical isolation -------------------------------------------------------------------------------------

def test_owner_cannot_see_another_practitioners_athletes(world):
    w = world
    r = STATE["a"].send("POST", "/api/athletes", json={"code": "A-001", "display_name": "Isolated Athlete", "sport": "Cricket"})
    assert r.status_code == 201, r.text
    athlete_id = r.json()["id"]
    assert any(x["id"] == athlete_id for x in STATE["a"].get("/api/athletes").json())
    owner = STATE["owner"]
    assert all(x["id"] != athlete_id for x in owner.get("/api/athletes").json())
    assert owner.get(f"/api/athletes/{athlete_id}").status_code == 404
    # row-level security itself, below the application: the owner's database identity sees zero rows
    with w.h.admin_conn() as c:
        owner_id, tenant_id = c.execute("SELECT id, tenant_id FROM spps_dev.practitioners WHERE is_admin").fetchone()
    with w.h.role_conn("spps_dev_app") as c:
        with c.transaction():
            c.execute("SELECT set_config('spps.practitioner_id', %s, true), set_config('spps.tenant_id', %s, true)",
                      (str(owner_id), str(tenant_id)))
            assert c.execute("SELECT count(*) FROM spps_dev.athletes WHERE id = %s", (athlete_id,)).fetchone()[0] == 0
        with c.transaction():
            c.execute("SELECT set_config('spps.practitioner_id', %s, true), set_config('spps.tenant_id', %s, true)",
                      (str(STATE["a_id"]), str(tenant_id)))
            assert c.execute("SELECT count(*) FROM spps_dev.athletes WHERE id = %s", (athlete_id,)).fetchone()[0] == 1


# ---- 8. rejection and suspension -----------------------------------------------------------------------------------

def test_rejecting_an_approved_practitioner_delists_and_signs_them_out(world):
    w = world
    r = STATE["owner"].send("POST", f"/api/admin/practitioners/{STATE['b_id']}/approval",
                            json={"decision": "reject", "note": "Could not verify accreditation"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert STATE["b"].sign_in(STATE["b_uid"]).status_code == 403
    assert STATE["b"].get("/api/auth/me").status_code in (401, 403)
    assert "Dr Applicant Two" not in [p["name"] for p in _directory(w)]
    assert _directory(w)[0]["featured_owner"] is True


def test_non_admin_cannot_use_admin_endpoints(world):
    a = STATE["a"]
    assert a.get("/api/admin/practitioner-reviews").status_code == 403
    r = a.send("POST", f"/api/admin/practitioners/{STATE['b_id']}/approval", json={"decision": "approve", "note": "Credentials checked"})
    assert r.status_code == 403
    r = a.send("POST", f"/api/admin/practitioners/{STATE['a_id']}/approval", json={"decision": "approve", "note": "Credentials checked"})
    assert r.status_code == 403, "nobody approves themselves"


def test_owner_cannot_decide_on_their_own_profile(world):
    owner = STATE["owner"]
    with world.h.admin_conn() as c:
        owner_id = c.execute("SELECT id FROM spps_dev.practitioners WHERE is_admin").fetchone()[0]
    r = owner.send("POST", f"/api/admin/practitioners/{owner_id}/approval", json={"decision": "reject", "note": "x"})
    assert r.status_code >= 400
    assert _directory(world)[0]["featured_owner"] is True
