"""Phase 9: the release preflight knows every flag, variable and migration; docs list every file in run order."""
from pathlib import Path

import pytest

from .test_v2_platform_contracts import _env

ROOT = Path(__file__).resolve().parents[1]
MAIL = {"SPPS_SMTP_HOST": "smtp.example.org", "SPPS_SMTP_USERNAME": "fictional", "SPPS_SMTP_PASSWORD": "fictional",
        "SPPS_SMTP_FROM": "no-reply@example.org", "SPPS_PUBLIC_BASE_URL": "https://app.example.org"}


def test_every_flag_is_known_to_the_preflight_and_documented():
    from server.v2 import flags, preflight
    deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    for name in flags.FLAGS:
        assert name in deployment, name
        assert any(name in line for line in preflight.describe(_env())), name
    for name in list(preflight.REQUIRED_HOSTED_VARS) + [v for d in preflight.FLAG_VARS.values() for v in d]:
        assert name in deployment, name
        assert name in (ROOT / "render.yaml").read_text() or name in (ROOT / ".env.production.example").read_text(), name


def test_every_v2_migration_is_checked_by_the_preflight():
    from server.v2 import preflight
    files = sorted(p.name for p in (ROOT / "migrations").glob("00*.sql") if int(p.name[:4]) >= 33)
    assert sorted(preflight.MIGRATION_OBJECTS) == files
    assert all(preflight.MIGRATION_OBJECTS[f] for f in files)
    for needed in preflight.FLAG_MIGRATIONS.values():
        assert set(needed) <= set(files)


def test_loop_and_self_serve_need_mail_and_an_https_base_url():
    from server.v2.preflight import PreflightError, validate_env
    on = {"SPPS_PORTAL_ENABLED": "true", "SPPS_ATHLETE_LOOP_ENABLED": "true"}
    with pytest.raises(PreflightError, match="SPPS_SMTP_HOST"):
        validate_env(_env(**on))
    with pytest.raises(PreflightError, match="SPPS_PUBLIC_BASE_URL"):
        validate_env(_env(**on, **{**MAIL, "SPPS_PUBLIC_BASE_URL": "http://insecure.example.org"}))
    validate_env(_env(**on, **MAIL))
    with pytest.raises(PreflightError, match="directory"):
        validate_env(_env(**on, **MAIL, SPPS_SELF_SERVE_ENABLED="true"))
    with pytest.raises(PreflightError, match="SPPS_CRISIS_CONTACTS_TEXT"):
        validate_env(_env(**on, **MAIL, SPPS_SELF_SERVE_ENABLED="true", SPPS_DIRECTORY_ENABLED="true"))
    validate_env(_env(**on, **MAIL, SPPS_SELF_SERVE_ENABLED="true", SPPS_DIRECTORY_ENABLED="true",
                      SPPS_CRISIS_CONTACTS_TEXT="Fictional helpline 0000 (24/7)"))


def test_describe_never_prints_secret_values():
    from server.v2.preflight import describe
    secret = "s3cr3t-fictional-" + "x" * 30
    lines = "\n".join(describe(_env(SPPS_FORM_TOKEN_SECRET=secret, SPPS_SMTP_PASSWORD=secret)))
    assert secret not in lines and "SPPS_FORM_TOKEN_SECRET: set" in lines


def test_launch_documents_exist_and_cover_the_required_points():
    launch = (ROOT / "LAUNCH_CHECKLIST.md").read_text(encoding="utf-8").lower()
    for phrase in ("counsel", "children", "licen", "crisis", "rotate", "/health", "backup"):
        assert phrase in launch, phrase
    dep = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    serial = sorted(p.name for p in (ROOT / "supabase" / "sql").glob("[0-9][0-9]_*.sql") if p.name[:2] >= "34")
    for name in serial + ["v2_verify.sql"]:
        assert name in dep, name
    assert dep.index("SPPS_ATHLETE_LOOP_ENABLED=true") < dep.index("SPPS_DIRECTORY_ENABLED=true")
    assert "Rollback" in dep and ".ps1" in dep
