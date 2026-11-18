"""Hosted protected RLS profile (``SPPS_HOSTED_RLS=true``) for staging and production.

Runs the same least-privilege PostgreSQL architecture as the isolated local milestone
(FORCE RLS, transaction-local practitioner identity, separate auth role, separate identity
database reached only through SECURITY DEFINER functions / RLS projections) against a managed,
TLS-only PostgreSQL 17 instance (Render). It never relaxes a guard of the local profile:

* production is permitted only with the separate security and release gates;
* every connection must use TLS (``sslmode`` require / verify-ca / verify-full, never weaker);
* each URL must name the exact expected role and database; no other URL options;
* the clinical schema keeps its historical identifier ``spps_dev`` and the identity schema
  ``spps_identity_dev`` because every migration and SECURITY DEFINER function names them.
  They are identifiers, not an environment indicator;
* the pilot stays locked (HTTP 503 / WebSocket 1008) until the owner separately sets
  ``MODEL_C_HOSTED_PILOT_UNLOCKED=true``. Production is governed by its own release gate.

Database names are configurable because a managed instance chooses them at creation time.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from sqlalchemy.engine import URL, make_url

CLINICAL_SCHEMA = "spps_dev"
IDENTITY_SCHEMA = "spps_identity_dev"
CLINICAL_APP_ROLE = "spps_dev_app"
CLINICAL_AUTH_ROLE = "spps_dev_auth"
IDENTITY_AUTH_ROLE = "spps_identity_auth"
IDENTITY_PORTAL_ROLE = "spps_identity_portal"
DEFAULT_CLINICAL_DATABASE = "spps_dev"
DEFAULT_IDENTITY_DATABASE = "spps_dev_identity"

SAFE_SSLMODES = ("require", "verify-ca", "verify-full")
ALLOWED_QUERY = frozenset({"sslmode", "sslrootcert"})
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{2,62}")
_PROJECT_REF = re.compile(r"[a-z0-9]{20}")  # Supabase project reference


class HostedProfileError(RuntimeError):
    """Refusal to start or provision the hosted profile. Never contains credentials."""


def enabled() -> bool:
    return os.getenv("SPPS_HOSTED_RLS", "false").lower() == "true"


def rls_profile() -> bool:
    """True for every profile that uses restricted roles + FORCE RLS (local milestone or hosted)."""
    return enabled() or os.getenv("MODEL_C_INTEGRATION_DEV", "false").lower() == "true"


def _identifier(value: str, what: str) -> str:
    if not _IDENTIFIER.fullmatch(value or "") or value in ("postgres", "template0", "template1"):
        raise HostedProfileError(f"Invalid {what}")
    return value


def database_names(env=None) -> tuple[str, str]:
    env = os.environ if env is None else env
    single=env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower()=="true"
    raw_clinical=env.get("SPPS_CLINICAL_DATABASE_NAME",DEFAULT_CLINICAL_DATABASE)
    raw_identity=env.get("SPPS_IDENTITY_DATABASE_NAME",DEFAULT_IDENTITY_DATABASE)
    # Supabase's primary database is named postgres; this exception is limited
    # to the explicitly selected, verified single-database profile.
    if single and raw_clinical==raw_identity=="postgres":
        if not _PROJECT_REF.fullmatch(env.get("SPPS_SUPABASE_PROJECT_REF") or ""):
            raise HostedProfileError("Single-database postgres target requires SPPS_SUPABASE_PROJECT_REF")
        return "postgres","postgres"
    clinical = _identifier(raw_clinical, "clinical database name")
    identity = _identifier(raw_identity, "identity database name")
    if clinical == identity and env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower() != "true":
        raise HostedProfileError("Clinical and identity data must use separate databases unless schema isolation is enabled")
    if clinical != identity and env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower() == "true":
        raise HostedProfileError("Single-database mode requires one exact database name")
    return clinical, identity


def normalize(raw: str) -> URL:
    raw = raw or ""
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            raw = "postgresql+psycopg://" + raw[len(prefix):]
    try:
        return make_url(raw)
    except Exception:  # noqa: BLE001 - any parse error is a refusal without echoing the URL
        raise HostedProfileError("Unparseable hosted database URL") from None


def sslmode(url: URL) -> str:
    mode = url.query.get("sslmode", "require")
    if isinstance(mode, tuple):
        raise HostedProfileError("Duplicate sslmode")
    if mode not in SAFE_SSLMODES:
        raise HostedProfileError("Hosted databases require TLS (sslmode require, verify-ca or verify-full)")
    return mode


def require_hosted_database(raw: str, *, role: str, database: str, env=None) -> URL:
    """Validate (never connect) a hosted URL for exactly one role and database."""
    if not raw:
        raise HostedProfileError("Hosted profile requires a PostgreSQL (psycopg) URL")
    url = normalize(raw)
    if url.get_backend_name() != "postgresql" or url.drivername != "postgresql+psycopg":
        raise HostedProfileError("Hosted profile requires PostgreSQL (psycopg)")
    if not url.host or not url.password:
        raise HostedProfileError("Hosted database URL requires a host and password")
    env = os.environ if env is None else env
    expected=role
    # Supabase session pooler (port 5432) authenticates as "<role>.<project ref>".
    ref = env.get("SPPS_SUPABASE_PROJECT_REF") or ""
    if (env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower()=="true"
        and _PROJECT_REF.fullmatch(ref)
        and url.host.endswith(".pooler.supabase.com")
        and url.port==5432):
        expected=role+"."+ref
    if url.username != expected:
        raise HostedProfileError(f"Hosted database URL must authenticate as {role}")
    if url.database != database:
        raise HostedProfileError("Hosted database URL names an unexpected database")
    if set(url.query) - ALLOWED_QUERY:
        raise HostedProfileError("Hosted database URL options are restricted to sslmode/sslrootcert")
    sslmode(url)
    return url


def connect_args(url: URL, *, schema: str | None = None) -> dict:
    """psycopg keyword arguments that can only strengthen the URL's TLS settings."""
    args = {"sslmode": sslmode(url)}
    if schema:
        args["options"] = f"-c search_path={_identifier(schema, 'schema')}"
    return args


def trusted_proxy_hops(env=None) -> int:
    env = os.environ if env is None else env
    raw = env.get("SPPS_TRUSTED_PROXY_HOPS", "0")
    if raw not in ("0", "1", "2", "3"):
        raise HostedProfileError("SPPS_TRUSTED_PROXY_HOPS must be 0-3")
    return int(raw)


def client_ip(request) -> str:
    """Throttle key. Hops=0: socket peer. Hops=N: the N-th address from the RIGHT of X-Forwarded-For,
    i.e. the one appended by the trusted platform proxy, never a client-supplied leftmost value."""
    peer = request.client.host if request.client else "unknown"
    hops = trusted_proxy_hops()
    if not hops:
        return peer
    chain = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(chain) < hops:
        return peer
    candidate = chain[-hops][:64]
    return candidate if re.fullmatch(r"[0-9A-Fa-f:.]{2,64}", candidate) else peer


def validate_startup(env=None) -> dict:
    """All refusals for the hosted RLS profile. Returns validated URLs keyed by purpose."""
    env = os.environ if env is None else env
    mode = env.get("MODEL_C_ENV", "local").lower()
    if mode not in ("staging", "production"):
        raise HostedProfileError("The hosted RLS profile requires staging or production")
    if mode == "production":
        required = {
            "SPPS_PRODUCTION_RELEASE_APPROVED": "true",
            "SPPS_HOSTED_RLS": "true",
            "SPPS_BETA_V1_FREE": "true",
            "SPPS_PAYMENT_PROVIDER": "disabled",
            "SPPS_SUPABASE_AUTH_ENABLED": "true",
            "SPPS_OWNER_BOOTSTRAP_ENABLED": "true",
        }
        if any(env.get(k, "").lower() != v for k,v in required.items()):
            raise HostedProfileError("Production release prerequisites are not satisfied")
        # Phase 1 launch: practitioner registration, owner approval, directory and
        # workspace. The athlete/guardian portal is an explicit opt-in and keeps its
        # own SMTP requirement (validate_portal). Supabase Auth sends verification
        # mail; the optional SPPS SMTP settings only add approval-decision e-mails.
        if env.get("SPPS_PORTAL_ENABLED", "false").lower() not in ("true", "false"):
            raise HostedProfileError("SPPS_PORTAL_ENABLED must be true or false")
        smtp = [bool(env.get(k)) for k in ("SPPS_SMTP_HOST", "SPPS_SMTP_USERNAME",
                                           "SPPS_SMTP_PASSWORD", "SPPS_SMTP_FROM")]
        if any(smtp) and not all(smtp):
            raise HostedProfileError("Production SMTP must be configured completely or not at all")
        if (env.get("SPPS_OWNER_EMAIL", "").strip().lower() != "karansportpsy@gmail.com"
            or not env.get("SPPS_PUBLIC_BASE_URL", "").startswith("https://")):
            raise HostedProfileError("Production requires owner identity and HTTPS configuration")
        if env.get("MODEL_C_PUBLIC_TENANT", "").strip() != "winmindperform":
            # The serial SQL creates only this tenant; the row-level policy admits no other public slug.
            raise HostedProfileError("Production requires MODEL_C_PUBLIC_TENANT=winmindperform")
    if env.get("MODEL_C_INTEGRATION_DEV", "false").lower() == "true":
        raise HostedProfileError("Hosted RLS and the local integration milestone are mutually exclusive")
    if env.get("SPPS_DEV_CLINICAL_PARITY", "false").lower() == "true":
        raise HostedProfileError("SPPS_DEV_CLINICAL_PARITY is a local-only switch")
    if env.get("MODEL_C_DB_SCHEMA", CLINICAL_SCHEMA) != CLINICAL_SCHEMA:
        raise HostedProfileError("Hosted RLS uses the protected clinical schema spps_dev only")
    trusted_proxy_hops(env)
    clinical, identity = database_names(env)
    app = require_hosted_database(env.get("DATABASE_URL", ""), role=CLINICAL_APP_ROLE, database=clinical, env=env)
    auth = require_hosted_database(env.get("SPPS_AUTH_DATABASE_URL", ""), role=CLINICAL_AUTH_ROLE, database=clinical, env=env)
    if (app.host, app.port) != (auth.host, auth.port):
        raise HostedProfileError("Clinical application and authentication roles must use the same database")
    urls = {"clinical_app": app, "clinical_auth": auth}
    if env.get("SPPS_PORTAL_ENABLED", "false").lower() == "true":
        urls.update(validate_portal(env, identity))
    elif env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower() == "true":
        # The identity roles can be validated while public guardian/athlete
        # routes stay disabled pending real SMTP and safeguarding acceptance.
        urls.update({
            "identity_auth":require_hosted_database(env.get("SPPS_IDENTITY_AUTH_DATABASE_URL",""),role=IDENTITY_AUTH_ROLE,database=identity,env=env),
            "identity_portal":require_hosted_database(env.get("SPPS_IDENTITY_PORTAL_DATABASE_URL",""),role=IDENTITY_PORTAL_ROLE,database=identity,env=env),
        })
    if env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower() == "true":
        # One physical database is acceptable only with distinct per-schema,
        # non-superuser/non-BYPASSRLS login roles; no shared app credentials.
        if not {"identity_auth","identity_portal"} <= set(urls):
            raise HostedProfileError("Single-database schema isolation requires portal identity roles")
        target = (app.host,app.port,app.database)
        if any((u.host,u.port,u.database) != target for u in urls.values()):
            raise HostedProfileError("All single-database connection roles must target the identical host, port and database")
        if len({u.username for u in urls.values()}) != len(urls):
            raise HostedProfileError("Clinical and identity connections must use distinct restricted login roles")
    # v2: every flag, its dependent flags and the variables it needs (server/v2/preflight.py). Checked last so the
    # v1 refusals keep their order and messages.
    from .v2.preflight import PreflightError, validate_env
    try:
        validate_env(env)
    except PreflightError as exc:
        raise HostedProfileError(str(exc)) from None
    return urls


def validate_portal(env, identity_database: str) -> dict:
    if env.get("SPPS_PORTAL_DEV_OUTBOX"):
        raise HostedProfileError("SPPS_PORTAL_DEV_OUTBOX is local-only; hosted e-mail uses the SMTP provider")
    if env.get("SPPS_NOTIFY_PROVIDER", "smtp") != "smtp":
        raise HostedProfileError("Hosted notification delivery requires the SMTP provider")
    if not env.get("SPPS_SMTP_HOST") or not env.get("SPPS_SMTP_SENDER"):
        raise HostedProfileError("Hosted portal requires SPPS_SMTP_HOST and SPPS_SMTP_SENDER")
    base = urlparse(env.get("SPPS_PUBLIC_BASE_URL", ""))
    allowed = {h.strip().lower() for h in env.get("MODEL_C_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if base.scheme != "https" or not base.hostname or base.hostname.lower() not in allowed or base.path not in ("", "/"):
        raise HostedProfileError("SPPS_PUBLIC_BASE_URL must be an https origin listed in MODEL_C_ALLOWED_HOSTS")
    if env.get("SPPS_BETA_V1_FREE", "false").lower() == "true":
        if env.get("SPPS_PAYMENT_PROVIDER", "disabled") != "disabled":
            raise HostedProfileError("SPPS Version 1.0 - Free requires SPPS_PAYMENT_PROVIDER=disabled")
    elif env.get("SPPS_PAYMENT_PROVIDER", "razorpay") != "razorpay":
        raise HostedProfileError("Hosted staging accepts only the Razorpay TEST adapter")
    return {
        "identity_auth": require_hosted_database(env.get("SPPS_IDENTITY_AUTH_DATABASE_URL", ""),
                                                 role=IDENTITY_AUTH_ROLE, database=identity_database, env=env),
        "identity_portal": require_hosted_database(env.get("SPPS_IDENTITY_PORTAL_DATABASE_URL", ""),
                                                   role=IDENTITY_PORTAL_ROLE, database=identity_database, env=env),
    }
