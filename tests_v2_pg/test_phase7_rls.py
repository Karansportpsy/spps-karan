"""Phase 7 on PostgreSQL: anonymous callers read only the snapshot; unlisting follows the account automatically."""
from __future__ import annotations

from .conftest import as_role, denied, q


def _snapshot(superuser, person):
    q(superuser, """INSERT INTO spps_dev.directory_snapshot(practitioner_id,tenant_id,display_name,qualifications,sports,
                    athlete_levels,age_groups,languages,delivery,city,taking_new_clients,about,badges,verified_until)
                    VALUES (%(p)s,%(t)s,'Fictional','MSc','Hockey','club','18-24','English','online','X',true,'',
                            '[]','2099-01-01') ON CONFLICT (practitioner_id) DO NOTHING""",
      {"p": person["id"], "t": person["tenant_id"]})


def test_anonymous_reads_snapshot_only_and_enquiry_objects_no_longer_exist(pgenv, people, superuser):
    _snapshot(superuser, people["b"])
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT count(*) FROM directory_snapshot WHERE practitioner_id=%(p)s", {"p": people["b"]["id"]})[0][0] == 1
        assert q(anon, "SELECT count(*) FROM directory_profiles")[0][0] == 0
        assert denied(anon, """INSERT INTO directory_snapshot(practitioner_id,tenant_id,display_name,qualifications,sports,
                               athlete_levels,age_groups,languages,delivery,city,taking_new_clients,about,badges,verified_until)
                               VALUES (%(p)s,%(t)s,'x','x','x','x','x','x','online','x',true,'','[]','2099-01-01')""",
                      {"p": people["a"]["id"], "t": people["a"]["tenant_id"]})
        anon.commit()
    # Booking-only directory: the enquiry tables and functions were dropped by migration 0042.
    gone = q(superuser, """SELECT (to_regclass('spps_dev.directory_enquiries') IS NULL)
                               AND (to_regclass('spps_dev.inquiries') IS NULL)
                               AND (to_regclass('spps_identity_dev.loop_requests') IS NULL)
                               AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname IN
                                   ('v2_owner_enquiry_stats','submit_public_enquiry'))""")[0][0]
    assert gone


def test_deactivating_or_unapproving_a_practitioner_unlists_them(pgenv, people, superuser):
    _snapshot(superuser, people["b"])
    q(superuser, "UPDATE spps_dev.practitioner_profiles SET status='pending_approval' WHERE practitioner_id=%(p)s",
      {"p": people["b"]["id"]})
    assert q(superuser, "SELECT count(*) FROM spps_dev.directory_snapshot WHERE practitioner_id=%(p)s",
             {"p": people["b"]["id"]})[0][0] == 0
    q(superuser, "UPDATE spps_dev.practitioner_profiles SET status='approved' WHERE practitioner_id=%(p)s", {"p": people["b"]["id"]})
    _snapshot(superuser, people["b"])
    q(superuser, "UPDATE spps_dev.practitioners SET active=false WHERE id=%(p)s", {"p": people["b"]["id"]})
    assert q(superuser, "SELECT count(*) FROM spps_dev.directory_snapshot WHERE practitioner_id=%(p)s",
             {"p": people["b"]["id"]})[0][0] == 0
    q(superuser, "UPDATE spps_dev.practitioners SET active=true WHERE id=%(p)s", {"p": people["b"]["id"]})


def test_only_the_owner_publishes_and_only_approved_active_practitioners(pgenv, people, superuser):
    ins = """INSERT INTO directory_snapshot(practitioner_id,tenant_id,display_name,qualifications,sports,athlete_levels,
             age_groups,languages,delivery,city,taking_new_clients,about,badges,verified_until)
             VALUES (%(p)s,%(t)s,'x','x','x','x','x','x','online','x',true,'','[]','2099-01-01')"""
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert denied(a, ins, {"p": people["a"]["id"], "t": people["a"]["tenant_id"]})  # not the owner
    q(superuser, "UPDATE spps_dev.practitioner_profiles SET status='pending_approval' WHERE practitioner_id=%(p)s",
      {"p": people["a"]["id"]})
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert denied(owner, ins, {"p": people["a"]["id"], "t": people["a"]["tenant_id"]})  # not approved
    q(superuser, "UPDATE spps_dev.practitioner_profiles SET status='approved' WHERE practitioner_id=%(p)s", {"p": people["a"]["id"]})
