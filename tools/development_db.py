"""Provision a NEW local fictional database schema; never touch hosted databases.

SPPS_DEV_ADMIN_URL must target an already-created, empty spps_dev* database on
loopback. Secrets are written only to ignored .env.integration; never printed.
"""
import os
from datetime import timedelta
from pathlib import Path
import secrets

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from server.db import Base
from server.identity import require_local_database
from server.models import Tenant, Practitioner, PractitionerProfile, PractitionerSubscription, Factor, Intervention, now
from server.security import make_password

ROOT = Path(__file__).resolve().parents[1]
CLINICAL_MIGRATIONS = ("0002_isolated_development_rls.sql", "0003_local_clinical_parity.sql",
                       "0010_clinical_slot_management.sql", "0014_clinical_admin_review.sql", "0017_free_clinical_slots.sql",
                        "0023_restricted_self_profile_edit.sql")


def provision(admin_url, *, passwords=None):
    target = require_local_database(admin_url)
    if os.getenv('MODEL_C_ENV', 'local') != 'local':
        raise RuntimeError('Development provisioning is local-only')
    credentials = passwords or {name: secrets.token_urlsafe(24) for name in ('app', 'auth', 'alice', 'bob', 'carol')}
    engine = create_engine(target.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as conn:
            if conn.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname='spps_dev'")):
                raise RuntimeError('Development schema already exists; refusing to modify it')
            if conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE c.relkind='r' AND n.nspname NOT IN ('pg_catalog','information_schema')")):
                raise RuntimeError('Development database must be empty')
            # Refuse existing cluster roles: never rotate or adopt unrelated credentials.
            for role in ('spps_dev_app', 'spps_dev_auth'):
                if conn.scalar(text('SELECT count(*) FROM pg_roles WHERE rolname=:r'), {'r': role}):
                    raise RuntimeError('Development role already exists; use a fresh isolated cluster')
            from psycopg import sql
            raw = conn.connection.driver_connection
            for suffix in ('app', 'auth'):
                raw.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB '
                                    'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS').format(
                    sql.Identifier('spps_dev_' + suffix), sql.Literal(credentials[suffix])))
            conn.execute(text('CREATE SCHEMA spps_dev'))
            conn.execute(text('SET LOCAL search_path=spps_dev'))
            Base.metadata.create_all(conn)
            with Session(bind=conn) as db:
                tenant = Tenant(slug='fictional-integration', display_name='Fictional Development Practice')
                db.add(tenant)
                db.flush()
                # alice/bob: clinical practitioners; carol: fictional tenant administrator (13D reviews only).
                for name in ('alice', 'bob', 'carol'):
                    user = Practitioner(tenant_id=tenant.id, email=f'{name}@spps.test',
                                        name=f'Fictional {name.title()}', password_hash=make_password(credentials[name]),
                                        tier='core', is_admin=name == 'carol')
                    db.add(user)
                    db.flush()
                    # Explicitly pre-approved fictional fixture identity. Listing
                    # remains withheld until independent credential + publication review.
                    db.add(PractitionerProfile(practitioner_id=user.id, qualifications=f'Fictional MSc ({name})',
                                               experience_years=5, public_approved=False, credentials_verified=False,
                                               status='approved', email_verified_at=now(), approved_at=now()))
                    db.add(PractitionerSubscription(practitioner_id=user.id, plan='core', amount_paise=19900,
                                                   status='active', test_mode=True, current_period_start=now(),
                                                   current_period_end=now()+timedelta(days=30)))
                db.add(Factor(tenant_id=tenant.id, label='Confidence', group_name='Fictional monitoring',
                              kind='monitoring', low=0, high=10, unit='points'))
                db.add(Intervention(tenant_id=tenant.id, name='Fictional pre-performance breathing',
                                    purpose='Fictional demonstration only', method='Practice a brief breathing reset',
                                    evidence_note='Synthetic reference entry; professional review required',
                                    reference_url='', approved=True))
                db.flush()
            raw.execute((ROOT / 'migrations/0002_isolated_development_rls.sql').read_text(encoding='utf-8'))
            raw.execute((ROOT / 'migrations/0003_local_clinical_parity.sql').read_text(encoding='utf-8'))
            raw.execute((ROOT / 'migrations/0010_clinical_slot_management.sql').read_text(encoding='utf-8'))
            raw.execute((ROOT / 'migrations/0014_clinical_admin_review.sql').read_text(encoding='utf-8'))
            # Apply the same free-slot and safe self-profile functions recorded
            # in the migration ledger. The checked-in files have BEGIN/COMMIT
            # wrappers for standalone psql execution; preserve this transaction.
            from server.release_migrations import sql_payload
            for migration in ('0017_free_clinical_slots.sql',
                              '0023_restricted_self_profile_edit.sql'):
                body,_ = sql_payload(ROOT / 'migrations' / migration)
                raw.execute(body)
            from tools.db_drill import record_ledger
            record_ledger(conn, CLINICAL_MIGRATIONS)
        def url(suffix):
            return target.set(drivername='postgresql+psycopg', username='spps_dev_'+suffix,
                              password=credentials[suffix]).render_as_string(hide_password=False)
        return {'MODEL_C_ENV': 'local', 'MODEL_C_INTEGRATION_DEV': 'true',
                'SPPS_DEV_CLINICAL_PARITY': 'true',
                'MODEL_C_INIT_DB': 'false', 'MODEL_C_SEED_DEMO': 'false',
                'MODEL_C_COOKIE_SECURE': 'false', 'MODEL_C_PAYMENT_TEST_MODE': 'false',
                'MODEL_C_ALLOW_TEST_ERASURE': 'false', 'MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION': 'false',
                'DATABASE_URL': url('app'), 'SPPS_AUTH_DATABASE_URL': url('auth'),
                'SPPS_DEV_ALICE_PASSWORD': credentials['alice'], 'SPPS_DEV_BOB_PASSWORD': credentials['bob'],
                'SPPS_DEV_CAROL_PASSWORD': credentials['carol']}
    finally:
        engine.dispose()


if __name__ == '__main__':
    destination = ROOT / '.env.integration'
    if destination.exists():
        raise SystemExit('Refusing to overwrite existing .env.integration')
    result = provision(os.environ['SPPS_DEV_ADMIN_URL'])
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write('\n'.join(f'{k}={v}' for k, v in result.items()) + '\n')
    print('Created 33 protected local tables, clinical-parity policies and two fictional practitioners. Credentials saved to ignored .env.integration.')
