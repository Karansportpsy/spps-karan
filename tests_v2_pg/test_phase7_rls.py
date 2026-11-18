"""Phase 7 on PostgreSQL: anonymous callers read only the snapshot; unlisting follows the account automatically."""
from __future__ import annotations

from .conftest import as_role, denied, q


def _snapshot(superuser, person):
    q(superuser, """INSERT INTO spps_dev.directory_snapshot(practitioner_id,tenant_id,display_name,qualifications,sports,
                    athlete_levels,age_groups,languages,delivery,city,taking_new_clients,about,badges,verified_until)
                    VALUES (%(p)s,%(t)s,'Fictional','MSc','Hockey','club','18-24','English','online','X',true,'',
                            '[]','2099-01-01') ON CONFLICT (practitioner_id) DO NOTHING""",
      {"p": person["id"], "t": person["tenant_id"]})


def test_anonymous_reads_snapshot_only_and_can_add_but_never_read_enquiries(pgenv, people, superuser):
    _snapshot(superuser, people["b"])
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT count(*) FROM directory_snapshot WHERE practitioner_id=%(p)s", {"p": people["b"]["id"]})[0][0] == 1
        assert q(anon, "SELECT count(*) FROM directory_profiles")[0][0] == 0
        q(anon, """INSERT INTO directory_enquiries(tenant_id,practitioner_id,name,contact,role,sport,message)
                   VALUES (%(t)s,%(p)s,'Fictional','x@example.org','athlete','Hockey','Hello')""",
          {"t": people["b"]["tenant_id"], "p": people["b"]["id"]})
        assert q(anon, "SELECT count(*) FROM directory_enquiries")[0][0] == 0
        assert denied(anon, """INSERT INTO directory_enquiries(tenant_id,practitioner_id,name,contact,role,sport,message)
                               VALUES (%(t)s,%(p)s,'F','x@example.org','athlete','H','Hi')""",
                      {"t": people["a"]["tenant_id"], "p": people["a"]["id"]})  # A is not listed
        assert denied(anon, """INSERT INTO directory_snapshot(practitioner_id,tenant_id,display_name,qualifications,sports,
                               athlete_levels,age_groups,languages,delivery,city,taking_new_clients,about,badges,verified_until)
                               VALUES (%(p)s,%(t)s,'x','x','x','x','x','x','online','x',true,'','[]','2099-01-01')""",
                      {"p": people["a"]["id"], "t": people["a"]["tenant_id"]})
        anon.commit()
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        assert q(b, "SELECT count(*) FROM directory_enquiries")[0][0] >= 1
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert q(a, "SELECT count(*) FROM directory_enquiries WHERE practitioner_id=%(p)s", {"p": people["b"]["id"]})[0][0] == 0
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert q(owner, "SELECT count(*) FROM directory_enquiries")[0][0] == 0  # rows: never
        assert any(r[0] == people["b"]["id"] for r in q(owner, "SELECT * FROM spps_dev.v2_owner_enquiry_stats()"))


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
