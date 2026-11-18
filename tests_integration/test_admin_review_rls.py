"""Stage 13D on PostgreSQL: admin review under FORCE RLS (API and database layers)."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from server.identity import bind_identity
from server.models import Practitioner
from tests_integration.test_portal_links import practitioner


def ids(app):
    with app.state.AuthDB() as db:
        return {p.email.split("@")[0]: SimpleNamespace(id=p.id, tenant_id=p.tenant_id)
                for p in db.scalars(select(Practitioner))}


def test_admin_reviews_other_practitioners_api(app, integration_env):
    carol = practitioner(app, integration_env, "carol")
    people = ids(app)
    queue = {r["practitioner_id"]: r for r in carol.get("/api/admin/practitioner-reviews").json()}
    assert set(queue) >= {people["alice"].id, people["bob"].id, people["carol"].id}
    assert queue[people["carol"].id]["self"] is True and queue[people["bob"].id]["membership_active"] is True
    bob = people["bob"].id
    r = carol.post(f"/api/admin/practitioners/{bob}/publication", json={"decision": "approve", "reason": "Fictional publication"})
    assert r.status_code == 409  # credentials not yet verified
    r = carol.post(f"/api/admin/practitioners/{bob}/credentials", json={"decision": "verify", "reason": "Fictional registry check"})
    assert r.status_code == 200 and r.json()["credential_status"] == "verified"
    r = carol.post(f"/api/admin/practitioners/{bob}/publication", json={"decision": "approve", "reason": "Fictional publication"})
    assert r.status_code == 200 and r.json()["listed"] is True
    r = carol.post(f"/api/admin/practitioners/{bob}/credentials", json={"decision": "suspend", "reason": "Fictional complaint"})
    assert r.json()["credential_status"] == "suspended" and r.json()["publication_status"] == "withdrawn"
    assert r.json()["listed"] is False
    assert carol.post(f"/api/admin/practitioners/{people['carol'].id}/credentials",
                      json={"decision": "verify", "reason": "Self review"}).status_code == 403


def test_non_admin_practitioners_cannot_review(app, integration_env):
    alice = practitioner(app, integration_env, "alice")
    people = ids(app)
    assert alice.get("/api/admin/practitioner-reviews").status_code == 403
    assert alice.post(f"/api/admin/practitioners/{people['bob'].id}/credentials",
                      json={"decision": "verify", "reason": "Not an admin"}).status_code == 403


def test_database_enforces_admin_scope_and_no_self_review(app, integration_env):
    people = ids(app)
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    with app.state.DB() as db:  # only own profile readable; independent verification untouched
        bind_identity(db, alice)
        rows = db.execute(text("SELECT practitioner_id FROM practitioner_profiles")).scalars().all()
        assert rows == [alice.id]
        assert db.execute(text("UPDATE practitioner_profiles SET credentials_verified=true WHERE practitioner_id=:p"),
                          {"p": bob.id}).rowcount == 0
        # Self-edits use the restricted function, never direct UPDATE grants. It
        # always resets verified/public flags for independent re-approval.
        changed = db.scalar(text("""
            SELECT spps_dev.update_own_practitioner_profile(
                :qualifications,:experience_years,:achievements,:specialisms)
        """), {"qualifications":"Updated fictional MSc Sport Psychology",
               "experience_years":5,"achievements":"Fictional practice",
               "specialisms":"Performance and return to training"})
        assert changed == alice.id
        updated = db.execute(text("""
            SELECT qualifications,credentials_verified,public_approved
            FROM practitioner_profiles WHERE practitioner_id=:p
        """), {"p":alice.id}).one()
        assert updated.qualifications == "Updated fictional MSc Sport Psychology"
        assert updated.credentials_verified is False and updated.public_approved is False
        assert db.execute(text("""
            UPDATE practitioner_profiles SET public_approved=true
            WHERE practitioner_id=:p
        """), {"p":alice.id}).rowcount == 0  # no practitioner self-approval
        db.commit()
    with app.state.DB() as db:
        bind_identity(db, alice)
        with pytest.raises(DBAPIError):  # cannot directly change another user's qualifications
            db.execute(text("""
                UPDATE practitioner_profiles SET qualifications='forged'
                WHERE practitioner_id=:p
            """), {"p":bob.id})
        db.rollback()
    with app.state.DB() as db:
        bind_identity(db, alice)
        with pytest.raises(DBAPIError):  # no direct qualifications grant, even for self
            db.execute(text("""
                UPDATE practitioner_profiles SET qualifications='forged'
                WHERE practitioner_id=:p
            """), {"p":alice.id})
        db.rollback()
    with app.state.DB() as db:  # admin: same-tenant read, other-only update, column-limited
        bind_identity(db, carol)
        visible = set(db.execute(text("SELECT practitioner_id FROM practitioner_profiles")).scalars())
        assert {alice.id, bob.id, carol.id} <= visible
        assert db.execute(text("UPDATE practitioner_profiles SET public_approved=true WHERE practitioner_id=:p"),
                          {"p": carol.id}).rowcount == 0  # no self-review at the database layer
        db.rollback()
    with app.state.DB() as db:
        bind_identity(db, carol)
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE practitioner_profiles SET qualifications='forged' WHERE practitioner_id=:p"),
                       {"p": bob.id})
    with app.state.DB() as db:  # forged tenant: admin scope disappears; only the own profile remains
        bind_identity(db, SimpleNamespace(id=carol.id, tenant_id=carol.tenant_id + 1000))
        assert db.execute(text("SELECT practitioner_id FROM practitioner_profiles")).scalars().all() == [carol.id]
        assert db.execute(text("UPDATE practitioner_profiles SET public_approved=true WHERE practitioner_id=:p"),
                          {"p": bob.id}).rowcount == 0
    with app.state.DB() as db:  # app role still cannot read the practitioners table directly
        bind_identity(db, carol)
        with pytest.raises(DBAPIError):
            db.execute(text("SELECT email FROM practitioners"))
