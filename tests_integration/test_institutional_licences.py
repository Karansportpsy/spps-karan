"""13D institutional licensing: seats grant membership only; limits enforced atomically."""
import threading

from sqlalchemy import create_engine, text

from tests_integration.test_admin_review_rls import ids
from tests_integration.test_portal_auth import client
from tests_integration.test_portal_links import practitioner
from tools import db_drill


def clinical_admin(sql, params=None):
    engine = create_engine(db_drill.admin_url("spps_dev"))
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def set_subscription(pid, active):
    clinical_admin("UPDATE spps_dev.practitioner_subscriptions SET current_period_end=now()+"
                   + ("interval '30 days'" if active else "interval '-1 second'") + " WHERE practitioner_id=:p", {"p": pid})


def can_sign_in(app, env, name):
    with client(app) as c:
        return c.post("/api/auth/login", json={"email": f"{name}@spps.test",
                      "password": env[f"SPPS_DEV_{name.upper()}_PASSWORD"], "terms_accepted": True}).status_code


def test_seat_grants_membership_until_revoked_or_suspended(app, integration_env):
    carol = practitioner(app, integration_env, "carol")
    people = ids(app)
    alice = people["alice"].id
    lid = carol.post("/api/admin/licences", json={"organisation_name": "Fictional Academy", "seats": 2}).json()["licence_id"]
    try:
        set_subscription(alice, False)
        assert can_sign_in(app, integration_env, "alice") == 403  # no paid membership, no seat
        assert carol.post(f"/api/admin/licences/{lid}/seats", json={"practitioner_id": alice}).status_code == 201
        assert can_sign_in(app, integration_env, "alice") == 200  # institutional seat = membership
        listing = [r for r in carol.get("/api/admin/practitioner-reviews").json() if r["practitioner_id"] == alice][0]
        assert listing["membership_active"] is True and listing["listed"] is False  # never publishes by itself
        assert carol.post(f"/api/admin/licences/{lid}/status", json={"status": "SUSPENDED"}).json()["status"] == "SUSPENDED"
        assert can_sign_in(app, integration_env, "alice") == 403
        carol.post(f"/api/admin/licences/{lid}/status", json={"status": "ACTIVE"})
        assert carol.post(f"/api/admin/licences/{lid}/seats/{alice}/revoke").status_code == 200
        assert can_sign_in(app, integration_env, "alice") == 403
    finally:
        set_subscription(alice, True)
        carol.post(f"/api/admin/licences/{lid}/status", json={"status": "ENDED"})


def test_seat_limit_is_atomic_and_admin_only(app, integration_env):
    carol = practitioner(app, integration_env, "carol")
    people = ids(app)
    lid = carol.post("/api/admin/licences", json={"organisation_name": "Fictional Club", "seats": 1}).json()["licence_id"]
    try:
        barrier, codes = threading.Barrier(2), []

        def assign(pid):
            barrier.wait()
            codes.append(carol.post(f"/api/admin/licences/{lid}/seats", json={"practitioner_id": pid}).status_code)

        threads = [threading.Thread(target=assign, args=(people[n].id,)) for n in ("alice", "bob")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(codes) == [201, 409]
        assigned = [x for x in carol.get("/api/admin/licences").json() if x["id"] == lid][0]["assigned"]
        assert len(assigned) == 1
        assert carol.post(f"/api/admin/licences/{lid}/seats", json={"practitioner_id": 99999999}).status_code == 404
        alice = practitioner(app, integration_env, "alice")
        assert alice.get("/api/admin/licences").status_code == 403
        assert alice.post("/api/admin/licences", json={"organisation_name": "Self-made", "seats": 50}).status_code == 403
        assert alice.post(f"/api/admin/licences/{lid}/seats", json={"practitioner_id": people["alice"].id}).status_code == 403
    finally:
        for holder in [x for x in carol.get("/api/admin/licences").json() if x["id"] == lid][0]["assigned"]:
            carol.post(f"/api/admin/licences/{lid}/seats/{holder}/revoke")
        carol.post(f"/api/admin/licences/{lid}/status", json={"status": "ENDED"})
