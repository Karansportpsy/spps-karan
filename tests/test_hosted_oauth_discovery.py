"""SPPS locked-staging OAuth discovery and first owner claim safety regression."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_locked_staging_serves_only_public_supabase_auth_config():
    src=(ROOT/'server/main.py').read_text(encoding='utf-8')
    assert "'/api/public/supabase-auth-config'" in src.split('public_preview = request.method')[1].split('\n')[0]
    assert "elif staging_locked and request.url.path not in ('/health/live','/health/ready') and not public_preview:" in src
    assert "@app.post('/api/auth/supabase-exchange')" in src
    # Do not grant access to the privileged token exchange while staging is locked.
    assert "'/api/auth/supabase-exchange'" not in src.split('public_preview = request.method')[1].split('\n')[0]

def test_verified_email_exchanges_aal1_session_without_google_or_mfa():
    js=(ROOT/'web/app.js').read_text(encoding='utf-8')
    start=js.index('async function handleSupabaseLogin()')
    end=js.index('function authHelpButtons()',start)
    handler=js[start:end]
    assert "sb.auth.getUser()" in handler
    assert "api('/api/auth/supabase-exchange'" in handler
    assert "auth.mfa." not in handler
    assert "beginGoogleAuth" not in handler
    assert "signInWithPassword" in js
