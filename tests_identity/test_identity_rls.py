"""Stage 13A.4 restricted PostgreSQL acceptance on the dedicated local identity DB.

Run only after the guarded one-time installer. All fixture accounts are fictional.
"""
from contextlib import contextmanager

import psycopg
from dotenv import dotenv_values
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import create_engine,text

from tools.identity_review_db import PORTAL,AUTH,OWNER,selected_target

# Default is the disposable spps-test-ephemeral cluster (55436), never the
# manually reviewed 55434 sandbox unless SPPS_IDENTITY_TARGET=review is explicit.
TARGET=selected_target()
ACCESS_FILE=TARGET.access_file
guarded_admin_url=TARGET.admin_url


def access():
    assert ACCESS_FILE.is_file(), "Install the isolated identity sandbox first"
    return dotenv_values(ACCESS_FILE)


@contextmanager
def db(role=PORTAL, token=None):
    guarded_admin_url()  # Reconfirm exact Compose identity, port and independent volume.
    secrets=access()
    key="PORTAL_PASSWORD" if role==PORTAL else "AUTH_PASSWORD"
    conn=psycopg.connect(host="127.0.0.1",port=TARGET.port,dbname="spps_dev_identity",
                         user=role,password=secrets["SPPS_IDENTITY_"+key],connect_timeout=5)
    try:
        with conn.transaction():
            if token:
                conn.execute("SELECT set_config(%s,%s,true)",
                             ("spps_identity.session_token",secrets["SPPS_IDENTITY_"+token]))
            yield conn
    finally:
        conn.close()


def visible(conn, table, columns="id"):
    assert table in {
        "platform_people","platform_accounts","platform_role_grants",
        "guardian_authorities","practitioner_athlete_links"
    }
    assert columns in {"id","id,person_id","id,practitioner_id"}
    return list(conn.execute(
        f"SELECT {columns} FROM spps_identity_dev.{table} ORDER BY id"
    ))


def test_eight_tables_have_force_rls_no_bypass_roles():
    engine=create_engine(guarded_admin_url())
    try:
        with engine.connect() as conn:
            rows=conn.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                       pg_get_userbyid(c.relowner)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='spps_identity_dev' AND c.relkind='r'
            """)).all()
            assert len(rows)==22
            assert all(en and force and owner==OWNER for _,en,force,owner in rows)
            roles=conn.execute(text("""
                SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin
                FROM pg_roles
                WHERE rolname IN ('spps_identity_owner','spps_identity_auth','spps_identity_portal')
            """)).all()
            assert len(roles)==3
            assert all(not sup and not dbrole and not createrole and not bypass
                       for _,sup,dbrole,createrole,bypass,_ in roles)
            assert next(login for name,*_,login in roles if name==OWNER) is False
            assert next(login for name,*_,login in roles if name==PORTAL) is True
    finally:
        engine.dispose()


def test_no_token_or_forged_account_id_returns_no_person():
    with db() as conn:
        assert visible(conn,"platform_accounts")==[]
        assert visible(conn,"platform_people")==[]
        assert visible(conn,"practitioner_athlete_links")==[]
        conn.execute("SELECT set_config(%s,%s,true)",("spps_identity.account_id","10"))
        assert visible(conn,"platform_accounts")==[]


def test_athlete_self_scope_and_athlete_isolation():
    with db(token="ALICE_TOKEN") as conn:
        assert visible(conn,"platform_accounts","id,person_id")==[(10,100)]
        assert visible(conn,"platform_people")==[(100,)]
        assert visible(conn,"platform_role_grants")==[(1,)]
        assert visible(conn,"practitioner_athlete_links","id,practitioner_id")==[(1,900)]
        assert visible(conn,"guardian_authorities")==[]
    with db(token="BOB_TOKEN") as conn:
        assert visible(conn,"platform_accounts","id,person_id")==[(11,101)]
        assert visible(conn,"platform_people")==[(101,)]
        assert visible(conn,"practitioner_athlete_links","id,practitioner_id")==[(4,902)]


def test_guardian_verified_only_and_scoped_link():
    with db(token="GUARDIAN_TOKEN") as conn:
        assert visible(conn,"platform_accounts","id,person_id")==[(12,200)]
        assert visible(conn,"guardian_authorities")==[(1,),(3,),(4,)]
        assert visible(conn,"platform_people")==[(300,)]
        assert visible(conn,"practitioner_athlete_links","id,practitioner_id")==[(2,900)]
    with db(token="UNRELATED_TOKEN") as conn:
        assert visible(conn,"platform_accounts")==[(13,)]
        assert visible(conn,"guardian_authorities")==[(2,)]
        assert visible(conn,"platform_people")==[]
        assert visible(conn,"practitioner_athlete_links")==[]


def test_withdrawn_consent_hides_old_guardian_link():
    with db(token="GUARDIAN_TOKEN") as conn:
        assert conn.execute(
            "SELECT count(*) FROM spps_identity_dev.practitioner_athlete_links "
            "WHERE practitioner_id=901"
        ).fetchone()[0]==0
        assert conn.execute(
            "SELECT count(*) FROM spps_identity_dev.practitioner_athlete_links "
            "WHERE id=2"
        ).fetchone()[0]==1


def test_expired_revoked_unverified_sessions_deny_everything():
    for token in ("EXPIRED_TOKEN","REVOKED_TOKEN","UNVERIFIED_TOKEN"):
        with db(token=token) as conn:
            assert visible(conn,"platform_people")==[]
            assert visible(conn,"platform_accounts")==[]
            assert visible(conn,"practitioner_athlete_links")==[]


def test_session_guc_does_not_leak_between_transactions():
    guarded_admin_url()
    secrets=access()
    with psycopg.connect(host="127.0.0.1",port=TARGET.port,dbname="spps_dev_identity",
                         user=PORTAL,password=secrets["SPPS_IDENTITY_PORTAL_PASSWORD"],
                         connect_timeout=5) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config(%s,%s,true)",
                         ("spps_identity.session_token",secrets["SPPS_IDENTITY_ALICE_TOKEN"]))
            assert visible(conn,"platform_accounts")==[(10,)]
        with conn.transaction():
            assert visible(conn,"platform_accounts")==[]


def denied(conn, statement):
    # Let the nested transaction roll back *before* continuing after SQL error.
    try:
        with conn.transaction():
            conn.execute(statement)
    except InsufficientPrivilege:
        return
    raise AssertionError("Insufficient-privilege guard did not fire")


def test_portal_cannot_read_secrets_modify_rows_or_escalate():
    with db(token="ALICE_TOKEN") as conn:
        for statement in (
            "SELECT password_hash FROM spps_identity_dev.platform_accounts",
            "SELECT token_hash FROM spps_identity_dev.platform_account_sessions",
            "SELECT * FROM spps_identity_dev.platform_verification_challenges",
            "SELECT * FROM spps_identity_dev.platform_consent_events",
            "SELECT evidence_reference FROM spps_identity_dev.guardian_authorities",
            "DELETE FROM spps_identity_dev.platform_people WHERE id=100",
            "UPDATE spps_identity_dev.platform_accounts SET status='DISABLED' WHERE id=10",
            "INSERT INTO spps_identity_dev.platform_people(id,created_at) VALUES (9999,now())",
            "ALTER TABLE spps_identity_dev.platform_people DISABLE ROW LEVEL SECURITY",
            "SET ROLE spps_identity_owner",
            "SET ROLE spps_identity_auth",
        ):
            denied(conn,statement)


def test_auth_role_has_no_table_access_only_functions():
    # 13A.5 tightening: the 13A.4 auth role could SELECT every account/session
    # row (all password and session hashes). It now has no table privileges.
    with db(role=AUTH) as conn:
        for statement in (
            "SELECT count(*) FROM spps_identity_dev.platform_accounts",
            "SELECT password_hash FROM spps_identity_dev.platform_accounts",
            "SELECT count(*) FROM spps_identity_dev.platform_account_sessions",
            "SELECT * FROM spps_identity_dev.platform_role_grants",
            "SELECT * FROM spps_identity_dev.platform_people",
            "SELECT * FROM spps_identity_dev.practitioner_athlete_links",
            "SELECT * FROM spps_identity_dev.platform_auth_throttle",
            "SELECT * FROM spps_identity_dev.platform_audit_events",
            "INSERT INTO spps_identity_dev.guardian_authorities(guardian_account_id,dependent_person_id,status,scope) VALUES (12,300,'VERIFIED','assigned_tasks')",
            "DELETE FROM spps_identity_dev.platform_accounts WHERE id=10",
            "UPDATE spps_identity_dev.platform_accounts SET status='VERIFIED'",
            "SET ROLE spps_identity_owner",
            "SET ROLE spps_identity_portal",
        ):
            denied(conn,statement)
