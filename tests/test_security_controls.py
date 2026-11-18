"""Phase F security controls: login oracles, request IDs, security headers, portal CSP."""
from fastapi.testclient import TestClient

import server.main as main_module
from test_support.app import create_app


def app_client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'sec.db'}", init_database=True, seed_demo=True)
    return TestClient(app)


def test_terms_checked_before_credentials_so_no_password_oracle(tmp_path):
    c = app_client(tmp_path)
    good = c.post('/api/auth/login', json={'email': 'practitioner@modelc.test', 'password': 'DemoOnly!2026'})
    bad = c.post('/api/auth/login', json={'email': 'practitioner@modelc.test', 'password': 'wrong-password'})
    assert good.status_code == bad.status_code == 422 and good.json() == bad.json()


def test_unknown_email_still_runs_password_hash(tmp_path, monkeypatch):
    c = app_client(tmp_path)
    calls = []
    real = main_module.verify_password
    monkeypatch.setattr(main_module, 'verify_password', lambda raw, enc: calls.append(enc) or real(raw, enc))
    unknown = c.post('/api/auth/login', json={'email': 'nobody@example.org', 'password': 'x', 'terms_accepted': True})
    known = c.post('/api/auth/login', json={'email': 'practitioner@modelc.test', 'password': 'x', 'terms_accepted': True})
    assert unknown.status_code == known.status_code == 401 and unknown.json() == known.json()
    assert len(calls) == 2 and calls[0] == main_module.DUMMY_PASSWORD_HASH


def test_request_id_and_security_headers(tmp_path):
    c = app_client(tmp_path)
    r = c.get('/health/live', headers={'X-Request-ID': 'fictional-trace-1234'})
    assert r.headers['X-Request-ID'] == 'fictional-trace-1234'
    injected = c.get('/health/live', headers={'X-Request-ID': 'bad id\r\nSet-Cookie: x=1'})
    assert injected.headers['X-Request-ID'] != 'bad id' and len(injected.headers['X-Request-ID']) == 32
    for path in ('/', '/portal', '/api/public/services'):
        h = c.get(path).headers
        assert h['X-Frame-Options'] == 'DENY' and h['X-Content-Type-Options'] == 'nosniff'
        assert "frame-ancestors 'none'" in h['Content-Security-Policy'] and "object-src 'none'" in h['Content-Security-Policy']
        assert 'geolocation=()' in h['Permissions-Policy']
    assert c.get('/api/public/services').headers['Cache-Control'] == 'no-store'


def test_request_log_contains_no_query_or_secrets(tmp_path, caplog):
    c = app_client(tmp_path)
    with caplog.at_level('INFO', logger='spps.request'):
        c.get('/api/public/services?email=person@example.org&token=secret-value')
    line = [r.getMessage() for r in caplog.records if r.name == 'spps.request'][-1]
    assert '"path": "/api/public/services"' in line
    assert 'person@example.org' not in line and 'secret-value' not in line


def test_portal_page_served_with_strict_csp(tmp_path):
    c = app_client(tmp_path)
    r = c.get('/portal')
    assert r.status_code == 200 and 'portal.js' in r.text
    csp = r.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp and "script-src 'self' https://checkout.razorpay.com" in csp
