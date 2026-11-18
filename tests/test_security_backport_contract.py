"""Security fixes from main must survive production branch consolidation."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_supabase_verified_email_without_totp():
    s=(ROOT/"server/supabase_practitioner_auth.py").read_text()
    assert 'user.get("email_confirmed_at")' in s
    assert 'Use an email/password account, not social sign-in' in s
    assert 'Authenticator-app TOTP verification is required' not in s

def test_stale_reads_never_erase_verified_clinical_writes():
    s=(ROOT/"web/app.js").read_text()
    assert "clinicalWriteRevision" in s
    assert "writeRevision!==clinicalWriteRevision" in s
    assert "const writeRevision=++clinicalWriteRevision" in s
    assert (ROOT/"web/app.js").read_bytes()==(ROOT/"www/assets/app.js").read_bytes()
