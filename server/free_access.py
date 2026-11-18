"""Feature-gated free software access for SPPS Version 1.0 - Free.

Do not enable this flag on a hosted service until free-booking SQL migrations,
RLS policies, portal identity, mail delivery, and release acceptance pass.
The legacy paid release continues to function while this branch is validated.
"""
import os

FREE_FEATURES = (
    "athletes", "directory", "bookings", "video",
    "monitoring", "assessments", "assessment_trends", "offline_assessments",
    "load_recovery", "injury_psychology", "report_drafts",
    "reports", "report_exports", "interventions",
)

def enabled() -> bool:
    return os.environ.get("SPPS_BETA_V1_FREE", "false").strip().lower() == "true"

def active_practitioner(user) -> bool:
    return bool(user and user.active)

def free_tier_response() -> dict:
    return {
        "tier": "free",
        "prices_inr_per_month": {},
        "amount_paise": 0,
        "currency": "INR",
        "subscription_required": False,
        "payment_required": False,
        "features": {**{name: True for name in FREE_FEATURES},
                     "ai": False, "ai_eligible": False},
        "ai_note": "AI is not available in SPPS Version 1.0 - Free.",
        "test_payment_enabled": False,
    }
