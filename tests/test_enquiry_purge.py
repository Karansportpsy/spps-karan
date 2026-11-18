"""Booking-only directory: the enquiry feature is gone from routes, models, web and the allowlist, and the drop
migration is guarded so it can never destroy rows silently."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENQUIRY_ROUTES = ("/api/public/enquiries", "/api/enquiries", "/api/v2/directory/enquiries",
                  "/api/v2/admin/directory/enquiry-stats", "/api/v2/portal/enquiries")


def test_no_enquiry_routes_are_declared_in_server_source():
    pat = re.compile(r"""@\w+\.(get|post|put|patch|delete)\(\s*["'](/api/(public/)?enquiries|/api/v2/directory/enquiries|"""
                     r"""/api/v2/admin/directory/enquiry-stats|/api/v2/portal/enquiries|/api/v2/public/directory/[^"']*enquiries)""")
    for f in (ROOT / "server").rglob("*.py"):
        assert not pat.search(f.read_text(encoding="utf-8")), f.name


def test_no_enquiry_orm_models():
    from server.db import Base
    from server.v2.identity_models import V2IdentityBase
    from server.v2.models import V2Base
    names = set(Base.metadata.tables) | set(V2Base.metadata.tables) | set(V2IdentityBase.metadata.tables)
    assert not ({"inquiries", "directory_enquiries", "loop_requests"} & names)


def test_web_has_no_practitioner_enquiry_flow():
    pat = re.compile(r"/api/(public/)?enquiries|data-enquire|f=enquiry|account-enquiry|enquiry-stats|portal/enquiries")
    for f in (ROOT / "web").glob("*.js"):
        assert not pat.search(f.read_text(encoding="utf-8")), f.name


def test_allowlist_has_no_enquiry_patterns():
    text = (ROOT / "server/v2/allowlist.py").read_text(encoding="utf-8")
    assert "enquir" not in text.lower()


def test_drop_migration_is_guarded_and_forward_only():
    sql = (ROOT / "migrations/0042_v2_appointment_only_directory.sql").read_text(encoding="utf-8")
    assert "Enquiry rows exist" in sql and "RAISE EXCEPTION" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP TABLE")
    for t in ("directory_enquiries", "inquiries", "loop_requests"):
        assert f"DROP TABLE IF EXISTS" in sql and t in sql
    assert "CASCADE" not in sql.upper().replace("ON DELETE CASCADE", "")
