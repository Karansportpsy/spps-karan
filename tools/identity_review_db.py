"""Guarded, one-time Stage-13A.4 installer. Never run on canonical/hosted DB.

Creates eight ORM tables, restricted PostgreSQL roles, FORCE RLS and fictional
fixtures ONLY on the independently verified identity-review Docker project.
No import-time SQL/Docker action and no new FastAPI routes.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from server.platform_identity_models import IdentityBase
from tools.identity_review_preflight import (
    COMPOSE_PROJECT, REVIEW_DATABASE, REVIEW_PORT, REVIEW_SCHEMA,
    check_review_compose, require_review_admin_url,
)
from tools import ephemeral_guard

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.identity-review.yml"
ACCESS_FILE = ROOT / ".env.identity-review-access"
CONTAINER = "spps-identity-review-database-1"
VOLUME = "spps-identity-review_identity_review_data"
OWNER = "spps_identity_owner"
PORTAL = "spps_identity_portal"
AUTH = "spps_identity_auth"
# 13A.4 eight tables + 13A.5 platform_auth_throttle.
IDENTITY_TABLES = 22
IDENTITY_MIGRATIONS = ("0004_identity_review_roles_rls.sql", "0005_identity_auth_functions.sql",
                       "0006_guardian_authority_consent.sql", "0007_practitioner_links.sql",
                       "0008_link_invitation_preview.sql",
                       "0009_approval_first_booking.sql",
                       "0011_clinical_participation.sql",
                       "0012_platform_video.sql",
                       "0013_notification_delivery.sql",
                       "0015_institutional_licences.sql",
                       "0016_video_room_closure.sql", "0018_free_booking_lifecycle.sql")


def guarded_admin_url() -> URL:
    """Admin URL for the manual 55434 identity REVIEW sandbox (unchanged guard)."""
    if check_review_compose(COMPOSE):
        raise RuntimeError("STOP: isolated identity Compose preflight failed")
    # CI uses a fresh Postgres service mapped to the exact same local port.
    in_ci = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_REPOSITORY") == "karansportpsy-spec/spps-beta15-pilot"
        and os.getenv("SPPS_IDENTITY_CI_SERVICE") == "1"
    )
    if not in_ci:
        try:
            data = json.loads(subprocess.check_output(
                ["docker", "inspect", CONTAINER], text=True,
                stderr=subprocess.DEVNULL, timeout=15,
            ))[0]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, ValueError, IndexError, KeyError):
            raise RuntimeError("STOP: identity sandbox container could not be verified") from None
        labels = data.get("Config", {}).get("Labels", {}) or {}
        ports = (data.get("HostConfig", {}).get("PortBindings", {}) or {}).get("5432/tcp") or []
        valid_volume = any(
            v.get("Type") == "volume" and v.get("Name") == VOLUME
            and v.get("Destination") == "/var/lib/postgresql/data"
            for v in data.get("Mounts", [])
        )
        if not (
            labels.get("com.docker.compose.project") == COMPOSE_PROJECT
            and labels.get("com.docker.compose.service") == "database"
            and data.get("State", {}).get("Running") is True
            and data.get("State", {}).get("Health", {}).get("Status") == "healthy"
            and len(ports) == 1
            and ports[0].get("HostIp") == "127.0.0.1"
            and ports[0].get("HostPort") == str(REVIEW_PORT)
            and valid_volume
        ):
            raise RuntimeError("STOP: wrong container, volume, port mapping or health")
    password = os.getenv("SPPS_IDENTITY_REVIEW_PASSWORD") or dotenv_values(
        ROOT / ".env.identity-review"
    ).get("SPPS_IDENTITY_REVIEW_PASSWORD")
    if not password:
        raise RuntimeError("STOP: missing isolated identity admin password")
    url = URL.create("postgresql+psycopg", username="postgres", password=password,
                     host="127.0.0.1", port=REVIEW_PORT, database=REVIEW_DATABASE)
    return require_review_admin_url(url.render_as_string(hide_password=False))


EPHEMERAL_DIR = ROOT / "output" / ("test-ephemeral" + (f"-{ephemeral_guard.SLOT}" if ephemeral_guard.SLOT else ""))


def ephemeral_admin_url() -> URL:
    """Admin URL for spps_dev_identity inside the verified disposable cluster."""
    password = os.getenv("SPPS_EPHEMERAL_PASSWORD") or dotenv_values(
        EPHEMERAL_DIR / "admin.env"
    ).get("SPPS_EPHEMERAL_PASSWORD")
    if not password:
        raise RuntimeError("STOP: missing ephemeral admin password (start it with tools.ephemeral_db up)")
    url = URL.create("postgresql+psycopg", username="postgres", password=password,
                     host="127.0.0.1", port=ephemeral_guard.PORT,
                     database=ephemeral_guard.IDENTITY_DATABASE)
    try:
        return ephemeral_guard.verify_ephemeral_target(
            url.render_as_string(hide_password=False),
            database=ephemeral_guard.IDENTITY_DATABASE)
    except ephemeral_guard.EphemeralGuardError as exc:
        raise RuntimeError(str(exc)) from None


@dataclass(frozen=True)
class IdentityTarget:
    name: str
    port: int
    access_file: Path
    admin_url: object  # zero-argument callable returning a verified URL


REVIEW_TARGET = IdentityTarget("review", REVIEW_PORT, ROOT / ".env.identity-review-access",
                               guarded_admin_url)
EPHEMERAL_TARGET = IdentityTarget("ephemeral", ephemeral_guard.PORT,
                                  EPHEMERAL_DIR / "identity-access.env", ephemeral_admin_url)
TARGETS = {t.name: t for t in (REVIEW_TARGET, EPHEMERAL_TARGET)}


def selected_target() -> IdentityTarget:
    """Automated tests default to the disposable cluster, never the review sandbox."""
    name = os.getenv("SPPS_IDENTITY_TARGET", "ephemeral")
    if name not in TARGETS:
        raise RuntimeError("STOP: unknown identity target")
    return TARGETS[name]


def seed_fictional(conn, tokens: dict[str, str]) -> None:
    # Fixed synthetic IDs; no legacy athlete records or real identities imported.
    conn.execute(text("""
        INSERT INTO spps_identity_dev.platform_people(id,created_at)
        VALUES (100,now()),(101,now()),(200,now()),(201,now()),
               (202,now()),(300,now()),(301,now()),(302,now())
    """))
    conn.execute(text("""
        INSERT INTO spps_identity_dev.platform_accounts
        (id,person_id,email_normalized,password_hash,status,verified_at,created_at)
        VALUES
        (10,100,'fictional-athlete-a@example.invalid','not-a-real-login-hash','VERIFIED',now(),now()),
        (11,101,'fictional-athlete-b@example.invalid','not-a-real-login-hash','VERIFIED',now(),now()),
        (12,200,'fictional-guardian@example.invalid','not-a-real-login-hash','VERIFIED',now(),now()),
        (13,201,'fictional-unrelated@example.invalid','not-a-real-login-hash','VERIFIED',now(),now()),
        (14,202,'fictional-unverified@example.invalid','not-a-real-login-hash','REGISTERED_UNVERIFIED',NULL,now())
    """))
    conn.execute(text("""
        INSERT INTO spps_identity_dev.platform_role_grants
        (id,account_id,role,status,created_at)
        VALUES (1,10,'ATHLETE','ACTIVE',now()),(2,11,'ATHLETE','ACTIVE',now()),
               (3,12,'GUARDIAN','ACTIVE',now()),(4,13,'GUARDIAN','ACTIVE',now()),
               (5,14,'ATHLETE','ACTIVE',now())
    """))
    conn.execute(text("""
        INSERT INTO spps_identity_dev.guardian_authorities
        (id,guardian_account_id,dependent_person_id,status,scope,verified_at,expires_at,revoked_at)
        VALUES
        (1,12,300,'VERIFIED','assigned_tasks',now(),now()+interval '30 days',NULL),
        (2,13,300,'PENDING_VERIFICATION','assigned_tasks',NULL,NULL,NULL),
        (3,12,301,'REVOKED','assigned_tasks',now(),NULL,now()),
        (4,12,302,'VERIFIED','assigned_tasks',now()-interval '40 days',now()-interval '1 day',NULL)
    """))
    conn.execute(text("""
        INSERT INTO spps_identity_dev.platform_consent_events
        (id,person_id,actor_account_id,guardian_authority_id,practitioner_id,
         purpose,policy_version,decision,recorded_at)
        VALUES
        (1,100,10,NULL,900,'CASE_LINK','fictional-v1','GRANTED',now()-interval '10 days'),
        (2,300,12,1,900,'CASE_LINK','fictional-v1','GRANTED',now()-interval '9 days'),
        (3,301,12,3,900,'CASE_LINK','fictional-v1','GRANTED',now()-interval '8 days'),
        (4,300,12,1,901,'CASE_LINK','fictional-v1','GRANTED',now()-interval '7 days'),
        (5,300,12,1,901,'CASE_LINK','fictional-v1','WITHDRAWN',now()-interval '6 days'),
        (6,101,11,NULL,902,'CASE_LINK','fictional-v1','GRANTED',now()-interval '5 days'),
        (7,302,12,4,900,'CASE_LINK','fictional-v1','GRANTED',now()-interval '4 days')
    """))
    conn.execute(text("""
        INSERT INTO spps_identity_dev.practitioner_athlete_links
        (id,person_id,practitioner_id,athlete_record_id,consent_event_id,
         status,approved_at,activated_at)
        VALUES
        (1,100,900,500,1,'ACTIVE',now(),now()),
        (2,300,900,501,2,'ACTIVE',now(),now()),
        (3,300,901,502,4,'ACTIVE',now(),now()),
        (4,101,902,503,6,'ACTIVE',now(),now()),
        (5,301,900,504,3,'ACTIVE',now(),now()),
        (6,302,900,505,7,'ACTIVE',now(),now())
    """))
    sessions = [
        (1000,10,"ALICE_TOKEN","now()+interval '1 day'","NULL"),
        (1001,11,"BOB_TOKEN","now()+interval '1 day'","NULL"),
        (1002,12,"GUARDIAN_TOKEN","now()+interval '1 day'","NULL"),
        (1003,13,"UNRELATED_TOKEN","now()+interval '1 day'","NULL"),
        (1004,10,"REVOKED_TOKEN","now()+interval '1 day'","now()"),
        (1005,10,"EXPIRED_TOKEN","now()-interval '1 day'","NULL"),
        (1006,14,"UNVERIFIED_TOKEN","now()+interval '1 day'","NULL"),
    ]
    _seed_sessions(conn, tokens, sessions)
    # Fixtures use explicit IDs; advance every SERIAL so later inserts
    # (13A.5 registration, sessions, challenges) never collide with them.
    for table in IdentityBase.metadata.sorted_tables:
        if "id" not in table.c:
            continue
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table.fullname}','id'), "
            f"GREATEST((SELECT COALESCE(max(id),0) FROM {table.fullname}),1))"
        ))


def _seed_sessions(conn, tokens, sessions):
    for row_id,account_id,key,expiry,revoked in sessions:
        conn.execute(text(f"""
            INSERT INTO spps_identity_dev.platform_account_sessions
            (id,account_id,token_hash,csrf_hash,expires_at,revoked_at,created_at)
            VALUES (:id,:account,:hashed,:csrf,{expiry},{revoked},now())
        """), {
            "id": row_id,"account": account_id,
            "hashed":hashlib.sha256(tokens[key].encode()).hexdigest(),
            "csrf":hashlib.sha256((tokens[key]+"-csrf").encode()).hexdigest(),
        })


def install(target: IdentityTarget = REVIEW_TARGET) -> None:
    access_file = target.access_file
    if access_file.exists():
        raise RuntimeError("STOP: existing identity access file; never overwrite or retry blindly")
    admin_url = target.admin_url()
    secrets_map = {f"{name}_PASSWORD":secrets.token_urlsafe(32)
                   for name in ("PORTAL","AUTH")}
    secrets_map.update({
        key:secrets.token_urlsafe(32)
        for key in ("ALICE_TOKEN","BOB_TOKEN","GUARDIAN_TOKEN","UNRELATED_TOKEN",
                    "REVOKED_TOKEN","EXPIRED_TOKEN","UNVERIFIED_TOKEN")
    })
    engine=create_engine(admin_url)
    try:
        with engine.begin() as conn:
            if conn.scalar(text("SELECT current_database()")) != REVIEW_DATABASE:
                raise RuntimeError("STOP: wrong PostgreSQL database")
            if conn.scalar(text("SELECT inet_server_port()")) != 5432:
                raise RuntimeError("STOP: wrong PostgreSQL service port")
            if conn.scalar(text("""
                SELECT count(*) FROM pg_tables
                WHERE schemaname NOT IN ('pg_catalog','information_schema')
            """)):
                raise RuntimeError("STOP: sandbox already contains user tables")
            if conn.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:s"),
                           {"s":REVIEW_SCHEMA}):
                raise RuntimeError("STOP: identity schema already exists")
            for role in (OWNER,PORTAL,AUTH):
                if conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname=:r"),
                               {"r":role}):
                    raise RuntimeError("STOP: identity role already exists")
            if not conn.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")):
                raise RuntimeError("STOP: installer requires isolated database superuser")
            raw=conn.connection.driver_connection
            raw.execute(sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(OWNER)))
            for role,key in ((PORTAL,"PORTAL_PASSWORD"),(AUTH,"AUTH_PASSWORD")):
                raw.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(role),sql.Literal(secrets_map[key])))
            raw.execute("CREATE SCHEMA spps_identity_dev")
            if len(IdentityBase.metadata.tables)!=IDENTITY_TABLES:
                raise RuntimeError("STOP: unexpected identity ORM table count")
            IdentityBase.metadata.create_all(conn)
            for migration in IDENTITY_MIGRATIONS:
                raw.execute((ROOT/"migrations"/migration).read_text(encoding="utf-8"))
            from tools.db_drill import record_ledger
            record_ledger(conn, IDENTITY_MIGRATIONS)
            raw.execute("ALTER SCHEMA spps_identity_dev OWNER TO spps_identity_owner")
            seed_fictional(conn,secrets_map)
            n=conn.scalar(text("""
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='spps_identity_dev' AND c.relkind='r'
                  AND c.relrowsecurity AND c.relforcerowsecurity
            """))
            if n!=IDENTITY_TABLES:raise RuntimeError("STOP: every identity table must FORCE RLS")
        # DB transaction committed; local access file is ignored and created exclusively.
        access_file.parent.mkdir(parents=True, exist_ok=True)
        fd=os.open(access_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            for name,value in secrets_map.items():
                stream.write(f"SPPS_IDENTITY_{name}={value}\n")
    finally:
        engine.dispose()
    print(f"PASS: {IDENTITY_TABLES} separate identity tables FORCE RLS; owner/auth/portal roles restricted.")
    print(f"Synthetic test accounts only; secrets in ignored {access_file.name} ({target.name} target).")
    print("No clinical schema or hosted resources changed. No runtime identity API enabled.")


if __name__=="__main__":
    args=sys.argv[1:]
    if args==["install"]:
        chosen=REVIEW_TARGET
    elif args==["install","--target","ephemeral"]:
        chosen=EPHEMERAL_TARGET
    else:
        raise SystemExit("Usage: python -m tools.identity_review_db install [--target ephemeral]")
    try:
        install(chosen)
    except Exception as exc:
        if isinstance(exc,RuntimeError):
            raise SystemExit("INSTALL STOP: "+str(exc))
        raise SystemExit("INSTALL STOP: database installation failed; inspect locally without sharing credentials")
