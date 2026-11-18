"""Release 1.0 - Free hardening on PostgreSQL (migration 0041). Fictional data only.

Written for the database-testing stage; run with the local harness:
    python tools/v2_pg_harness.py up && python -m pytest tests_v2_pg -q && python tools/v2_pg_harness.py down
"""
from __future__ import annotations

import secrets

from .conftest import as_role, denied, q
from .test_phase5_rls import _signed_report


def _open_link(superuser, person):
    rec, rid = _signed_report(superuser, person, "coach")
    t, p = person["tenant_id"], person["id"]
    cid = q(superuser, """INSERT INTO spps_dev.report_consents(tenant_id,practitioner_id,athlete_id,audience,given_by,method)
                          VALUES (%(t)s,%(p)s,%(a)s,'coach','athlete','written') RETURNING id""",
            {"t": t, "p": p, "a": rec["athlete_id"]})[0][0]
    h = secrets.token_hex(32)
    lid = q(superuser, """INSERT INTO spps_dev.report_links(tenant_id,practitioner_id,report_id,consent_id,token_hash,
                          expires_at,created_at) VALUES (%(t)s,%(p)s,%(r)s,%(c)s,%(h)s,now()+interval '7 days',now())
                          RETURNING id""", {"t": t, "p": p, "r": rid, "c": cid, "h": h})[0][0]
    return lid, h


def _open(pgenv, h):
    with as_role(pgenv, "spps_dev_app") as anon:
        out = q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": h})[0][0]
        anon.commit()
    return out


def test_link_closes_while_the_practitioner_is_inactive_or_not_approved(pgenv, people, superuser):
    pb = people["b"]["id"]
    lid, h = _open_link(superuser, people["b"])
    assert _open(pgenv, h) == "enc:v1:k1:x"
    q(superuser, "UPDATE spps_dev.practitioners SET active=false WHERE id=%(p)s", {"p": pb})
    assert _open(pgenv, h) is None
    q(superuser, "UPDATE spps_dev.practitioners SET active=true WHERE id=%(p)s", {"p": pb})
    assert _open(pgenv, h) == "enc:v1:k1:x"
    has_profile = q(superuser, "SELECT count(*) FROM spps_dev.practitioner_profiles WHERE practitioner_id=%(p)s",
                    {"p": pb})[0][0]
    assert has_profile, "the fixture practitioner must have a profile row for this check"
    original = q(superuser, "SELECT status FROM spps_dev.practitioner_profiles WHERE practitioner_id=%(p)s", {"p": pb})[0][0]
    try:
        for status in ("rejected", "pending_approval"):
            q(superuser, "UPDATE spps_dev.practitioner_profiles SET status=%(s)s WHERE practitioner_id=%(p)s",
              {"s": status, "p": pb})
            assert _open(pgenv, h) is None, status
    finally:
        q(superuser, "UPDATE spps_dev.practitioner_profiles SET status=%(s)s WHERE practitioner_id=%(p)s",
          {"s": original, "p": pb})
    outcomes = [r[0] for r in q(superuser, "SELECT outcome FROM spps_dev.report_link_views WHERE link_id=%(l)s ORDER BY id",
                                {"l": lid})]
    assert outcomes[:3] == ["shown", "revoked", "shown"] and outcomes[3:] == ["revoked", "revoked"]


def test_0041_keeps_the_function_closed_to_every_other_role(pgenv, people, superuser):
    lid, h = _open_link(superuser, people["b"])
    for role in ("spps_dev_auth", "spps_identity_auth", "spps_identity_portal"):
        with as_role(pgenv, role) as conn:
            assert denied(conn, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": h}), role
    definer = q(superuser, """SELECT p.prosecdef, array_to_string(p.proconfig, ',')
                              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                              WHERE n.nspname='spps_dev' AND p.proname='v2_open_report_link'""")
    assert len(definer) == 1 and definer[0][0] is True and "search_path=pg_catalog" in definer[0][1]
    public = q(superuser, """SELECT has_function_privilege('public', 'spps_dev.v2_open_report_link(text)', 'EXECUTE')""")
    assert public[0][0] is False
    with as_role(pgenv, "spps_dev_app") as anon:  # anonymous redemption still sees no clinical table
        for table in ("shared_reports", "report_links", "report_consents", "athletes", "notes"):
            assert q(anon, f"SELECT count(*) FROM {table}")[0][0] == 0, table
        anon.commit()
