"""Non-secret, read-only environment readiness summary.

Checks variable *presence* in this process only. NEVER prints variable values,
reads .env files, calls providers, unlocks hosting, or validates real credentials.
Run: python -m tools.check_integration_dependencies
"""
from __future__ import annotations
import os

SERVICES = {
    "postgresql_hosted": ["DATABASE_URL", "MODEL_C_ALLOWED_HOSTS", "MODEL_C_DB_SCHEMA"],
    "razorpay_sandbox": ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
                         "RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_PLAN_CORE_ID",
                         "RAZORPAY_PLAN_PRO_ID"],
    "webrtc_turn": ["MODEL_C_TURN_URL", "MODEL_C_TURN_USERNAME",
                   "MODEL_C_TURN_CREDENTIAL"],
}


def summarize(source=None):
    env = dict(os.environ if source is None else source)
    result = {}
    for label, names in SERVICES.items():
        missing = [name for name in names if not env.get(name, "").strip()]
        result[label] = {"all_present": not missing, "missing_names": missing}
    return result


def main():
    print("SPPS environment dependency inspection (no secret values, no network)")
    for label, finding in summarize().items():
        print(f"{label}: {'present/unverified' if finding['all_present'] else 'not configured'}")
        if finding["missing_names"]:
            print("  missing variable names: " + ", ".join(finding["missing_names"]))
    print("email_delivery: MISSING IMPLEMENTATION (no SMTP or email delivery module)")
    print("mobile_push: MISSING IMPLEMENTATION (no FCM or push token delivery module)")
    print("athlete_guardian_auth: MISSING IMPLEMENTATION (not fixed by API keys)")
    print("hosted_staging: LOCKED by default; this command cannot unlock it")
    print("Presence does not verify sandbox/live mode, provider credentials, webhook or TLS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
