"""Stage 13A.5a: auth SECURITY DEFINER functions on the disposable identity DB.

Fictional accounts only (@example.invalid). Every test creates its own data.
"""
import hashlib
import secrets
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue, RaiseException
from sqlalchemy import create_engine, text

from server.security import make_password
from tests_identity.test_identity_rls import AUTH, PORTAL, TARGET, access, db, denied

if TARGET.name != "ephemeral":
    pytest.skip("13A.5 write tests run only on the disposable ephemeral target", allow_module_level=True)


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def call(fn, *args, role=AUTH):
    with db(role=role) as conn:
        placeholders = ",".join(["%s"] * len(args))
        return conn.execute(f"SELECT * FROM spps_identity_dev.{fn}({placeholders})", args).fetchall()


def scalar(fn, *args):
    return call(fn, *args)[0][0]


def admin(sql, params=None):
    engine = create_engine(TARGET.admin_url())
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def new_email():
    return f"fictional-{uuid4().hex[:12]}@example.invalid"


def register(email=None, role="ATHLETE", password="Fictional-pass-1234"):
    email = email or new_email()
    challenge = secrets.token_urlsafe(32)
    pw_hash = make_password(password)
    account = scalar("auth_register", email, pw_hash, "Fictional Person", role, True, h(challenge), 3600)
    return email, account, challenge, pw_hash


def verified(role="ATHLETE"):
    email, account, challenge, pw_hash = register(role=role)
    assert scalar("auth_verify_email", h(challenge)) == account
    return email, account, pw_hash


def session_for(account, pw_hash):
    token = secrets.token_urlsafe(32)
    assert scalar("auth_create_session", account, pw_hash, h(token), h("csrf" + token), 3600) is True
    return token


def test_registration_creates_unverified_account_and_hides_duplicates():
    email, account, _, _ = register()
    assert isinstance(account, int)
    assert scalar("auth_register", email.upper(), make_password("Another-pass-1234"), "Dup", "GUARDIAN",
                  True, h("x" + email), 3600) is None
    rows = admin("SELECT status, verified_at, adult_attested_at FROM spps_identity_dev.platform_accounts WHERE id=:i",
                 {"i": account})
    assert rows[0][0] == "REGISTERED_UNVERIFIED" and rows[0][1] is None and rows[0][2] is not None


@pytest.mark.parametrize("args", [
    ("not-an-email", "ATHLETE", True),
    (None, "ADMIN", True),
    (None, "PRACTITIONER", True),
    (None, "ATHLETE", False),   # minors/unattested cannot self-register
    (None, "GUARDIAN", False),
])
def test_invalid_or_minor_registration_rejected(args):
    email, role, adult = args
    with pytest.raises((InvalidParameterValue, RaiseException)):
        scalar("auth_register", email or new_email(), make_password("Fictional-pass-1234"), "Fictional",
               role, adult, h(uuid4().hex), 3600)


def test_plaintext_password_rejected_by_database():
    with pytest.raises((InvalidParameterValue, RaiseException)):
        scalar("auth_register", new_email(), "plaintext-password", "Fictional", "ATHLETE", True, h(uuid4().hex), 3600)


def test_unverified_account_cannot_get_session_and_verification_is_single_use():
    _, account, challenge, pw_hash = register()
    assert scalar("auth_create_session", account, pw_hash, h(uuid4().hex), h(uuid4().hex), 3600) is False
    assert scalar("auth_verify_email", h("wrong" + challenge)) is None
    assert scalar("auth_verify_email", h(challenge)) == account
    assert scalar("auth_verify_email", h(challenge)) is None  # replay


def test_expired_verification_challenge_denied():
    _, account, challenge, _ = register()
    admin("UPDATE spps_identity_dev.platform_verification_challenges SET expires_at=now()-interval '1 second' "
          "WHERE token_hash=:t", {"t": h(challenge)})
    assert scalar("auth_verify_email", h(challenge)) is None


def test_resend_invalidates_previous_challenge():
    email, account, first, _ = register()
    second = secrets.token_urlsafe(32)
    assert scalar("auth_resend_verification", email, h(second), 3600) is True
    assert scalar("auth_verify_email", h(first)) is None
    assert scalar("auth_verify_email", h(second)) == account
    assert scalar("auth_resend_verification", email, h(uuid4().hex), 3600) is False  # already verified


def test_login_lookup_is_single_exact_email_only():
    email, account, pw_hash = verified()
    rows = call("auth_login_lookup", email)
    assert rows == [(account, pw_hash, "VERIFIED")]
    assert call("auth_login_lookup", "%") == []
    assert call("auth_login_lookup", "' OR 1=1 --") == []


def test_session_requires_current_password_hash_and_resolves_roles():
    _, account, pw_hash = verified(role="GUARDIAN")
    assert scalar("auth_create_session", account, make_password("stale"), h(uuid4().hex), h(uuid4().hex), 3600) is False
    token = session_for(account, pw_hash)
    row = call("auth_session", h(token), 1800)[0]
    assert row[1] == account and row[6] == ["GUARDIAN"] and row[5] == h("csrf" + token)
    assert call("auth_session", h("forged" + token), 1800) == []


def test_revocation_and_logout_all():
    _, account, pw_hash = verified()
    one, two = session_for(account, pw_hash), session_for(account, pw_hash)
    assert scalar("auth_revoke_session", h(one)) is True
    assert call("auth_session", h(one), 1800) == []
    assert call("auth_session", h(two), 1800) != []
    three = session_for(account, pw_hash)
    assert scalar("auth_revoke_all_sessions", h(three)) == 2
    assert call("auth_session", h(two), 1800) == [] and call("auth_session", h(three), 1800) == []


def test_idle_session_expires_and_is_revoked():
    _, account, pw_hash = verified()
    token = session_for(account, pw_hash)
    admin("UPDATE spps_identity_dev.platform_account_sessions SET last_seen_at=now()-interval '31 minutes' "
          "WHERE token_hash=:t", {"t": h(token)})
    assert call("auth_session", h(token), 1800) == []
    assert admin("SELECT revoked_at IS NOT NULL FROM spps_identity_dev.platform_account_sessions WHERE token_hash=:t",
                 {"t": h(token)})[0][0] is True


def test_at_most_ten_active_sessions():
    _, account, pw_hash = verified()
    tokens = [session_for(account, pw_hash) for _ in range(12)]
    active = [t for t in tokens if call("auth_session", h(t), 1800)]
    assert active == tokens[-10:]


def test_password_reset_single_use_revokes_sessions_and_ignores_unverified():
    email, account, pw_hash = verified()
    token = session_for(account, pw_hash)
    unverified_email, *_ = register()
    assert scalar("auth_request_password_reset", unverified_email, h(uuid4().hex), 1800) is False
    assert scalar("auth_request_password_reset", new_email(), h(uuid4().hex), 1800) is False
    reset = secrets.token_urlsafe(32)
    assert scalar("auth_request_password_reset", email, h(reset), 1800) is True
    new_hash = make_password("New-fictional-pass-5678")
    assert scalar("auth_reset_password", h(reset), "not-a-hash") is False
    assert scalar("auth_reset_password", h(reset), new_hash) is True
    assert scalar("auth_reset_password", h(reset), new_hash) is False  # replay
    assert call("auth_session", h(token), 1800) == []
    assert call("auth_login_lookup", email)[0][1] == new_hash


def test_newer_reset_request_invalidates_older():
    email, *_ = verified()
    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    assert scalar("auth_request_password_reset", email, h(first), 1800)
    assert scalar("auth_request_password_reset", email, h(second), 1800)
    assert scalar("auth_reset_password", h(first), make_password("Another-pass-1234")) is False


def test_change_password_keeps_only_current_session():
    _, account, pw_hash = verified()
    keep, other = session_for(account, pw_hash), session_for(account, pw_hash)
    new_hash = make_password("Changed-fictional-9999")
    assert scalar("auth_change_password", h(keep), make_password("wrong"), new_hash) is False
    assert scalar("auth_change_password", h(keep), pw_hash, new_hash) is True
    assert call("auth_session", h(keep), 1800) != [] and call("auth_session", h(other), 1800) == []


def test_throttle_window_limit_and_reset():
    key = h("login:" + uuid4().hex)
    assert [scalar("auth_throttle_hit", key, 600, 3) for _ in range(4)] == [True, True, True, False]
    admin("UPDATE spps_identity_dev.platform_auth_throttle SET window_start=now()-interval '11 minutes' "
          "WHERE key_hash=:k", {"k": key})
    assert scalar("auth_throttle_hit", key, 600, 3) is True
    with pytest.raises((InvalidParameterValue, RaiseException)):
        scalar("auth_throttle_hit", "raw-ip-10.0.0.1", 600, 3)


def test_portal_role_cannot_execute_auth_functions():
    with db(role=PORTAL) as conn:
        for statement in (
            "SELECT spps_identity_dev.auth_login_lookup('x@example.invalid')",
            "SELECT spps_identity_dev.auth_throttle_hit(repeat('a',64),60,1)",
            "SELECT * FROM spps_identity_dev.auth_session(repeat('a',64),60)",
        ):
            denied(conn, statement)


def test_new_session_works_with_portal_rls_projection():
    _, account, pw_hash = verified()
    token = session_for(account, pw_hash)
    secrets_map = access()
    with psycopg.connect(host="127.0.0.1", port=TARGET.port, dbname="spps_dev_identity", user=PORTAL,
                         password=secrets_map["SPPS_IDENTITY_PORTAL_PASSWORD"], connect_timeout=5) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('spps_identity.session_token',%s,true)", (token,))
            rows = conn.execute("SELECT id, display_name FROM spps_identity_dev.platform_accounts").fetchall()
    assert rows == [(account, "Fictional Person")]


def test_consent_ledger_is_append_only_even_for_superuser():
    with pytest.raises(Exception, match="append-only"):
        admin("UPDATE spps_identity_dev.platform_consent_events SET decision='WITHDRAWN' WHERE id=1")
    with pytest.raises(Exception, match="append-only"):
        admin("DELETE FROM spps_identity_dev.platform_consent_events WHERE id=1")


def test_no_delete_policy_exists_for_any_identity_table():
    rows = admin("SELECT count(*) FROM pg_policies WHERE schemaname='spps_identity_dev' AND cmd IN ('DELETE','ALL')")
    assert rows[0][0] == 0
