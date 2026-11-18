"""Stage 13D: credential verification, membership and publication are independent gates."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from test_support.app import create_app
from server.models import Audit, Practitioner, PractitionerProfile, PractitionerSubscription, now
from tests.test_paid_registration import reg, approve_synthetic_application


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV', 'local')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE', 'true')
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION', 'true')
    app = create_app(f"sqlite:///{tmp_path / 'd13.db'}", init_database=True, seed_demo=True)
    c = TestClient(app)
    tok = c.post('/api/public/practitioner-registration', json=reg(email='d13@example.org')).json()['registration_token']
    c.post('/api/internal/test-activate-subscription', json={'registration_token': tok})
    with app.state.DB() as db:
        pid = db.scalar(select(Practitioner.id).where(Practitioner.email == 'd13@example.org'))
    return app, c, pid


def login(c, email, password):
    r = c.post('/api/auth/login', json={'email': email, 'password': password, 'terms_accepted': True})
    assert r.status_code == 200, r.text
    return {'X-CSRF-Token': r.json()['csrf']}


def admin(c):
    return login(c, 'practitioner@modelc.test', 'DemoOnly!2026')


def names(c):
    return {p['name'] for p in c.get('/api/public/practitioners').json()}


def listed(app, c, pid):
    h = admin(c)
    assert c.post(f'/api/admin/practitioners/{pid}/credentials',
                  json={'decision': 'verify', 'reason': 'Fictional check'}, headers=h).status_code == 200
    assert c.post(f'/api/admin/practitioners/{pid}/publication',
                  json={'decision': 'approve', 'reason': 'Fictional approval'}, headers=h).status_code == 200
    with app.state.DB() as db:
        status=db.scalar(select(PractitionerProfile.status).where(
            PractitionerProfile.practitioner_id==pid))
    if status!='approved':
        # The first publication cannot bypass the separate pending account gate.
        assert 'Dr. Synthetic Registration' not in names(c)
        approve_synthetic_application(app,pid,c,h)
    # On re-verification after suspension, the account was already approved.
    # Re-applying the credential/publication review restores eligibility.
    return h


def expire(app, pid):
    with app.state.DB() as db:
        sub = db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id == pid))
        sub.current_period_end = now() - timedelta(seconds=1)
        db.commit()


def test_payment_alone_never_publishes_and_queue_shows_states(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    assert 'Dr. Synthetic Registration' not in names(c)
    h = admin(c)
    row = [r for r in c.get('/api/admin/practitioner-reviews', headers=h).json() if r['practitioner_id'] == pid][0]
    assert (row['credential_status'], row['membership_active'], row['publication_status'], row['listed']) == \
        ('submitted', True, 'pending_review', False)


def test_verified_credentials_alone_cannot_bypass_billing(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    expire(app, pid)
    h = admin(c)
    assert c.post(f'/api/admin/practitioners/{pid}/credentials',
                  json={'decision': 'verify', 'reason': 'Fictional check'}, headers=h).status_code == 200
    r = c.post(f'/api/admin/practitioners/{pid}/publication',
               json={'decision': 'approve', 'reason': 'Fictional approval'}, headers=h)
    assert r.status_code == 409 and 'membership' in r.json()['detail']
    assert 'Dr. Synthetic Registration' not in names(c)


def test_expiry_and_suspension_remove_listing_and_booking(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    h = listed(app, c, pid)
    assert 'Dr. Synthetic Registration' in names(c)
    assert c.get(f'/api/public/practitioners/{pid}/slots').status_code == 200
    assert c.post(f'/api/admin/practitioners/{pid}/credentials',
                  json={'decision': 'suspend', 'reason': 'Fictional complaint'}, headers=h).status_code == 200
    assert 'Dr. Synthetic Registration' not in names(c)
    assert c.get(f'/api/public/practitioners/{pid}/slots').status_code == 404
    with app.state.DB() as db:
        assert db.scalar(select(PractitionerProfile.public_approved)
                         .where(PractitionerProfile.practitioner_id == pid)) is False
    listed(app, c, pid)
    assert 'Dr. Synthetic Registration' in names(c)
    expire(app, pid)  # membership expiry unlists without any admin action
    assert 'Dr. Synthetic Registration' not in names(c)


def test_reviewer_separation_and_admin_only(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    h = admin(c)
    with app.state.DB() as db:
        admin_id = db.scalar(select(Practitioner.id).where(Practitioner.email == 'practitioner@modelc.test'))
    assert c.post(f'/api/admin/practitioners/{admin_id}/credentials',
                  json={'decision': 'verify', 'reason': 'Self review'}, headers=h).status_code == 403
    c.post('/api/auth/logout', headers=h)
    h2 = login(c, 'd13@example.org', 'AComplexDemoPass!2026')
    assert c.post(f'/api/admin/practitioners/{pid}/credentials',
                  json={'decision': 'verify', 'reason': 'Self promotion'}, headers=h2).status_code == 403
    assert c.post(f'/api/admin/practitioners/{pid}/publication',
                  json={'decision': 'approve', 'reason': 'Self promotion'}, headers=h2).status_code == 403
    assert c.get('/api/admin/practitioner-reviews', headers=h2).status_code == 403


def test_changed_qualifications_require_reverification_and_decisions_are_audited(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    h = listed(app, c, pid)
    c.post('/api/auth/logout', headers=h)
    h2 = login(c, 'd13@example.org', 'AComplexDemoPass!2026')
    r = c.put('/api/practitioner/profile', json={'qualifications': 'Fictional new doctorate', 'experience_years': 8,
                                                 'achievements': 'Fictional', 'specialisms': 'Fictional'}, headers=h2)
    assert r.status_code == 200, r.text
    assert 'Dr. Synthetic Registration' not in names(c)
    with app.state.DB() as db:
        pr = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == pid))
        assert (pr.credentials_verified, pr.public_approved) == (False, False)
        actions = set(db.scalars(select(Audit.action).where(Audit.object_type == 'practitioner', Audit.object_id == pid)))
    assert {'credential.verified', 'publication.approved'} <= actions


def test_institutional_quote_pipeline_transitions(tmp_path, monkeypatch):
    app, c, pid = setup(tmp_path, monkeypatch)
    r = c.post('/api/public/organisation-quote', json={
        'organisation_name': 'Fictional Academy', 'contact_name': 'Fictional Lead', 'contact_email': 'lead@example.org',
        'practitioner_seats': 4, 'athletes_estimate': 60, 'requirements': 'Fictional squad support', 'contact_consent': True})
    assert r.status_code == 201, r.text
    h = admin(c)
    qid = c.get('/api/admin/organisation-quotes', headers=h).json()[0]['id']
    assert c.post(f'/api/admin/organisation-quotes/{qid}/status', json={'status': 'accepted'}, headers=h).status_code == 409
    for status in ('contacted', 'proposal_sent', 'accepted', 'closed'):
        r = c.post(f'/api/admin/organisation-quotes/{qid}/status', json={'status': status}, headers=h)
        assert r.json()['status'] == status, r.text
