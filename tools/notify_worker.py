"""Notification delivery worker (run alongside the API; safe to run several copies).

    python -m tools.notify_worker --once          # one pass, print counts
    python -m tools.notify_worker --interval 30   # loop

Reads SPPS_IDENTITY_AUTH_DATABASE_URL (EXECUTE-only role) and SPPS_AUTH_DATABASE_URL
(practitioner e-mail lookup), SPPS_NOTIFY_PROVIDER and provider settings, and
SPPS_PUBLIC_BASE_URL for sign-in links. Messages never contain clinical content.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.models import Practitioner
from server.notifications import deliver_pending, provider_from_env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args(argv)
    mode = os.getenv("MODEL_C_ENV", "local").lower()
    identity = create_engine(os.environ["SPPS_IDENTITY_AUTH_DATABASE_URL"], pool_pre_ping=True)
    clinical_auth = create_engine(os.environ["SPPS_AUTH_DATABASE_URL"], pool_pre_ping=True,
                                  connect_args={"options": "-c search_path=spps_dev"})
    provider = provider_from_env(mode)
    base = os.getenv("SPPS_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

    def practitioner_email(pid):
        with Session(clinical_auth) as db:
            return db.scalar(select(Practitioner.email).where(Practitioner.id == pid))

    while True:
        result = deliver_pending(identity, provider, practitioner_email,
                                 portal_url=base + "/portal", workspace_url=base + "/studio")
        print(json.dumps({"event": "notify_pass", **result}), flush=True)
        if args.once:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
