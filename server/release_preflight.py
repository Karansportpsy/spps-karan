"""SPPS v1.0 production startup: verify structural and permission prerequisites.

The first verified platform owner must be able to claim their identity AFTER
startup through verified Supabase email/password Auth. Do not require an already-
provisioned owner row at startup: that causes a circular bootstrap failure.

No fallback to unprotected tables, privileged DB roles, or synthetic users.
"""
from __future__ import annotations

import os
from sqlalchemy import text
from .db import make_engine


def verify_production(app) -> None:
    if os.getenv("MODEL_C_ENV", "").lower() != "production":
        return

    with app.state.auth_engine.connect() as conn:
        valid = conn.scalar(text("""
          SELECT EXISTS(
            SELECT 1 FROM pg_roles
            WHERE rolname=current_user AND rolname='spps_dev_auth'
              AND NOT rolsuper AND NOT rolbypassrls
          )
        """))
        if not valid:
            raise RuntimeError("Production authentication role isolation check failed")

    with app.state.engine.connect() as conn:
        # Migrations and status-default are required before accepting registrations.
        names = set(conn.execute(text("""
          SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_dev'
            AND p.proname IN ('register_verified_free_practitioner',
                             'claim_verified_platform_owner',
                             'public_practitioner_directory', 'public_practitioner_photo',
                             'public_services', 'submit_public_enquiry',
                             'resubmit_own_application')
        """)).scalars())
        if names != {"register_verified_free_practitioner", "claim_verified_platform_owner",
                     "public_practitioner_directory", "public_practitioner_photo",
                     "public_services", "submit_public_enquiry", "resubmit_own_application"}:
            raise RuntimeError("Production registration, directory and verified-owner migrations (through 0029) are missing")
        granted = conn.scalar(text("""
          SELECT has_column_privilege('spps_dev_app',
                  'spps_dev.practitioner_profiles','status','UPDATE')
        """))
        if not granted:
            raise RuntimeError("Production admin approval permission migration is missing")
        default = conn.scalar(text("""
          SELECT column_default FROM information_schema.columns
          WHERE table_schema='spps_dev'
            AND table_name='practitioner_profiles' AND column_name='status'
        """))
        if not default or "pending_approval" not in default:
            raise RuntimeError("Production pending-approval status migration 0026 is missing")
        registration_function = conn.scalar(text("""
          SELECT pg_get_functiondef(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_dev'
            AND p.proname='register_verified_free_practitioner'
          LIMIT 1
        """))
        if not registration_function or "'pending_approval'" not in registration_function:
            raise RuntimeError("Verified signup must default to pending_approval at the database write-path")
        from . import account_lifecycle
        if account_lifecycle.enabled():
            # Switching the feature on before migration 0032 has been run would leave deactivated practitioners
            # in the public directory. Refuse to start instead of starting in that state.
            ready = conn.scalar(text("""
              SELECT EXISTS (SELECT 1 FROM pg_attribute a
                              WHERE a.attrelid='spps_dev.practitioner_profiles'::regclass
                                AND a.attname='deactivated_at' AND NOT a.attisdropped)
                 AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                              WHERE n.nspname='spps_dev' AND p.proname='set_own_account_deactivation'
                                AND has_function_privilege('spps_dev_app', p.oid, 'EXECUTE'))
                 AND (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                       WHERE n.nspname='spps_dev'
                         AND p.proname IN ('public_practitioner_directory','public_practitioner_photo')
                         AND pg_get_functiondef(p.oid) LIKE '%deactivated_at%') = 2
            """))
            if not ready:
                raise RuntimeError("SPPS_ACCOUNT_LIFECYCLE_ENABLED is true but migration 0032 "
                                   "(supabase/sql/33_0032_account_lifecycle.sql) has not been run")
        # v2: every v2 migration's objects, and the migrations behind each flag that is switched on.
        from .v2.preflight import PreflightError, verify_database
        try:
            verify_database(conn)
        except PreflightError as exc:
            raise RuntimeError(str(exc)) from None

    portal_url = os.getenv("SPPS_IDENTITY_PORTAL_DATABASE_URL", "")
    if not portal_url:
        raise RuntimeError("Production identity database connection is missing")
    engine = make_engine(portal_url)
    try:
        with engine.connect() as conn:
            functions = set(conn.execute(text("""
                SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='spps_identity_dev'
                  AND p.proname IN ('free_portal_request_booking',
                                   'free_practitioner_decide_booking',
                                   'free_practitioner_propose_reschedule',
                                   'free_portal_respond_reschedule')
            """)).scalars())
            if len(functions) != 4:
                raise RuntimeError("Production free-booking migration is missing")
    finally:
        engine.dispose()
