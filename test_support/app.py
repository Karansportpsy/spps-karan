"""The application under test, optionally seeded with fictional fixtures.

`create_app` mirrors the signature the tests have always used. The real application factory
(server.main.create_app) has no seeding and no payment simulation; both live here, for tests only.
"""
import os
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import server.main as main
from server import free_access
from server.models import Booking, Practitioner, SubscriptionCharge, now
from server.subscription_routes import active_membership


def seed_fixtures(app):
    from .demo_seed import ensure_demo_subscriptions, seed, seed_extended_demo, upgrade_synthetic_demo
    with app.state.DB() as db:
        seed(db)
        upgrade_synthetic_demo(db)
        seed_extended_demo(db)
        ensure_demo_subscriptions(db)


class _ActivationIn(BaseModel):
    registration_token: str = Field(min_length=35, max_length=150)


def install_provider_stand_ins(app):
    """Stand-ins for the payment provider's verified callbacks, so paid flows can be tested without a provider."""
    pay, subs = app.state.payment_internals, app.state.subscription_internals
    get_current, db_session, confirm_captured = pay["get_current"], pay["db_session"], pay["confirm_captured"]
    lookup_intent, event = subs["lookup_intent"], subs["event"]

    @app.post("/api/internal/test-capture/{bid}")
    def test_capture(bid: int, user: Practitioner = Depends(get_current), db: Session = Depends(db_session)):
        if free_access.enabled():
            raise HTTPException(410, "Payment workflows are disabled in free Beta v1.0")
        if os.getenv("MODEL_C_PAYMENT_TEST_MODE", "false") != "true" or not user.is_admin:
            raise HTTPException(404, "Test capture unavailable")
        b = db.scalar(select(Booking).where(Booking.id == bid, Booking.tenant_id == user.tenant_id))
        if not b:
            raise HTTPException(404, "Booking unavailable")
        record = confirm_captured(db, b, f"testpay_{bid}")
        return {"status": b.status, "practitioner_paise": record.practitioner_paise,
                "platform_paise": record.platform_paise, "payout_status": record.payout_status}

    @app.post("/api/internal/test-activate-subscription")
    def test_activate(payload: _ActivationIn, request: Request, db: Session = Depends(db_session)):
        if free_access.enabled():
            raise HTTPException(404, "Payment simulation is unavailable in free Beta v1.0")
        if (os.getenv("MODEL_C_PAYMENT_TEST_MODE", "false").lower() != "true"
                or os.getenv("MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION", "false").lower() != "true"):
            raise HTTPException(404, "Local subscription simulation disabled")
        user, sub = lookup_intent(db, payload.registration_token)
        if active_membership(db, user):
            return {"status": "active", "already_active": True}
        sub.status = "active"; sub.test_mode = True
        sub.current_period_start = now(); sub.current_period_end = now() + timedelta(days=30)
        user.tier = sub.plan
        db.add(SubscriptionCharge(subscription_id=sub.id, provider_payment_id="test_sub_" + str(sub.id),
                                  amount_paise=sub.amount_paise))
        event(db, user, "subscription.simulated", "subscription", sub.id)
        db.commit()
        return {"status": "active", "test_mode": True, "charged_real_money": False,
                "expires_at": sub.current_period_end.isoformat()}


def create_app(database_url=None, init_database=True, seed_demo=True):
    mode = os.getenv("MODEL_C_ENV", "local").lower()
    if seed_demo and (mode != "local" or os.getenv("MODEL_C_INTEGRATION_DEV", "false").lower() == "true"):
        raise RuntimeError("Fixtures are for the local test profile only (hosted mode refuses demo seed)")
    app = main.create_app(database_url, init_database=init_database)
    if mode == "local":
        install_provider_stand_ins(app)
    if seed_demo and init_database:
        seed_fixtures(app)
    return app
