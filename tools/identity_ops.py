"""Operator actions on the identity database (never exposed through the API).

    python -m tools.identity_ops grant-reviewer --email person@example.invalid [--target ephemeral]

Reviewer (independent guardian-authority verifier) roles cannot be self-registered.
An operator grants them to an already VERIFIED account. Only the verified identity
targets from tools.identity_review_db are accepted.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text


def grant_reviewer(admin_url, email: str) -> int:
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            account = conn.scalar(text(
                "SELECT id FROM spps_identity_dev.platform_accounts "
                "WHERE email_normalized=lower(btrim(:e)) AND status='VERIFIED'"), {"e": email})
            if account is None:
                raise RuntimeError("STOP: reviewer must be an existing VERIFIED account")
            exists = conn.scalar(text(
                "SELECT count(*) FROM spps_identity_dev.platform_role_grants "
                "WHERE account_id=:a AND role='REVIEWER' AND status='ACTIVE' AND revoked_at IS NULL"),
                {"a": account})
            if not exists:
                conn.execute(text(
                    "INSERT INTO spps_identity_dev.platform_role_grants(account_id,role,status,created_at) "
                    "VALUES (:a,'REVIEWER','ACTIVE',now())"), {"a": account})
                conn.execute(text(
                    "INSERT INTO spps_identity_dev.platform_audit_events"
                    "(actor_account_id,action,object_type,object_id,detail,recorded_at) "
                    "VALUES (NULL,'role.reviewer_granted_by_operator','account',:a,'',now())"), {"a": account})
            return account
    finally:
        engine.dispose()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant-reviewer")
    g.add_argument("--email", required=True)
    g.add_argument("--target", default="ephemeral", choices=["ephemeral"])
    args = ap.parse_args(argv)
    from tools.identity_review_db import TARGETS
    account = grant_reviewer(TARGETS[args.target].admin_url(), args.email)
    print(f"Reviewer role active for account {account}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        sys.exit(str(exc))
