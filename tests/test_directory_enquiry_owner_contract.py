"""Contract for migration 0029: public directory, enquiry, applicant resubmission, owner-safe profile edit."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL = (ROOT / "migrations/0029_public_directory_enquiry_owner_profile.sql").read_text(encoding="utf-8")


def _block(name):
    """Source of one CREATE OR REPLACE FUNCTION statement (up to the next statement of that kind or the grants)."""
    start = SQL.index(f"CREATE OR REPLACE FUNCTION spps_dev.{name}(")
    nxt = SQL.find("CREATE OR REPLACE FUNCTION", start + 10)
    grants = SQL.find("GRANT SELECT ON TABLE", start)
    ends = [e for e in (nxt, grants) if e != -1]
    return SQL[start:min(ends) if ends else len(SQL)]


FUNCTIONS = ("public_practitioner_directory", "public_practitioner_photo", "public_services",
             "resubmit_own_application", "update_own_practitioner_profile")


def test_0029_follows_0028_in_the_release_list_and_is_step_30_of_a_gap_free_serial_set():
    listed = (ROOT / "server/release_migrations.py").read_text(encoding="utf-8")
    assert listed.index("0028_production_public_tenant.sql") < listed.index("0029_public_directory_enquiry_owner_profile.sql")
    serial = sorted(p.name for p in (ROOT / "supabase/sql").glob("[0-9][0-9]_*.sql") if p.name[:2] not in ("98", "99"))
    assert serial[0].startswith("00_") and serial[30].startswith("30_0029_")
    assert [int(n[:2]) for n in serial] == list(range(len(serial))), "serial files must be gap-free from 00"
    assert len(serial) >= 31, "later numbered steps may follow 0029; none may be removed"
    assert (ROOT / "supabase/sql/98_verify.sql").exists() and (ROOT / "supabase/sql/99_set_role_passwords.sql").exists()
    body = (ROOT / "supabase/sql" / serial[30]).read_text(encoding="utf-8")
    core = SQL.strip().removeprefix("BEGIN;").removesuffix("COMMIT;").strip()
    assert core in body, "serial step 30 must carry migration 0029 verbatim (the builder merges the BEGIN/COMMIT wrapper)"
    assert body.count("BEGIN;") == 1 and body.rstrip().endswith("COMMIT;")


def test_preflight_requires_every_public_function():
    text = (ROOT / "server/release_preflight.py").read_text(encoding="utf-8")
    for name in ("public_practitioner_directory", "public_practitioner_photo", "public_services",
                 "resubmit_own_application"):
        assert f"'{name}'" in text


def test_every_definer_function_pins_search_path_and_is_not_public():
    for name in FUNCTIONS:
        head = _block(name)
        head = head[:head.index("AS $")]
        assert "SECURITY DEFINER" in head, name
        assert "SET search_path=pg_catalog,spps_dev" in head, name
        assert re.search(rf"REVOKE ALL ON FUNCTION spps_dev\.{name}\(", SQL), name
        grants = re.findall(rf"GRANT EXECUTE ON FUNCTION spps_dev\.{name}\([^;]*?\) TO ([a-z_]+);", SQL, re.S)
        assert grants == ["spps_dev_app"], (name, grants)


def test_no_grant_reaches_the_supabase_api_roles():
    assert not re.search(r"\b(anon|authenticated|service_role)\b", re.sub(r"--[^\n]*", "", SQL))


def test_directory_pins_the_verified_owner_first_and_returns_no_private_fields():
    block = _block("public_practitioner_directory")
    assert "ORDER BY (p.is_admin AND pr.supabase_auth_uid IS NOT NULL AND pr.email_verified_at IS NOT NULL) DESC" in block
    for gate in ("AND p.active", "pr.status = 'approved'", "AND pr.credentials_verified", "AND pr.public_approved"):
        assert gate in block, gate
    returns = block[block.index("RETURNS TABLE"):block.index("LANGUAGE")]
    for private in ("email", "password", "review_note", "supabase", "phone", "status"):
        assert private not in returns, private


def test_services_table_read_is_tenant_scoped_not_open():
    assert "GRANT SELECT ON TABLE spps_dev.services TO spps_dev_app" in SQL
    assert "services_same_tenant_read" in SQL and "tenant_id = spps_dev.tenant_id()" in SQL
    assert "USING (true)" not in SQL


def test_owner_profile_edit_keeps_owner_flags():
    block = _block("update_own_practitioner_profile")
    assert "is_admin" in block, "owner edits must branch on the admin flag so the listing and sign-in survive"
