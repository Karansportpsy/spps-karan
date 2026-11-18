"""Regression for v1.4.2. Creates fictional data in a disposable SQLite database."""
from fastapi.testclient import TestClient
from test_support.app import create_app


def login(client, email):
    res = client.post('/api/auth/login', json={
        'email': email, 'password': 'DemoOnly!2026', 'terms_accepted': True
    })
    assert res.status_code == 200, res.text
    return {'X-CSRF-Token': res.json()['csrf']}


def test_private_athlete_record_survives_restart_and_remains_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV', 'local')
    database = f'sqlite:///{tmp_path / "disposable-persistence.sqlite3"}'
    original = create_app(database, init_database=True, seed_demo=True)
    owner = TestClient(original)
    outsider = TestClient(original)
    headers = login(owner, 'practitioner@modelc.test')
    login(outsider, 'vikram@modelc.test')
    created = owner.post('/api/athletes', headers=headers, json={
        'code': 'SYN-142-01', 'display_name': 'Fictional Athlete 142',
        'sport': 'Badminton', 'service_consent_recorded': False
    })
    assert created.status_code == 201, created.text
    aid = created.json()['id']
    assert owner.get(f'/api/athletes/{aid}').status_code == 200
    assert outsider.get(f'/api/athletes/{aid}').status_code == 404
    restarted = create_app(database, init_database=True, seed_demo=False)
    new_owner = TestClient(restarted)
    new_outsider = TestClient(restarted)
    login(new_owner, 'practitioner@modelc.test')
    login(new_outsider, 'vikram@modelc.test')
    reread = new_owner.get(f'/api/athletes/{aid}')
    assert reread.status_code == 200 and reread.json()['athlete']['code'] == 'SYN-142-01'
    assert new_outsider.get(f'/api/athletes/{aid}').status_code == 404


def test_organisation_quotation_survives_restart_and_is_admin_only(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV', 'local')
    database = f'sqlite:///{tmp_path / "disposable-quote.sqlite3"}'
    app = create_app(database, init_database=True, seed_demo=True)
    guest = TestClient(app)
    result = guest.post('/api/public/organisation-quote', json={
        'organisation_name': 'Fictional Regional Sport Academy',
        'contact_name': 'Example Liaison',
        'contact_email': 'liaison@example.org',
        'practitioner_seats': 4, 'athletes_estimate': 75,
        'requirements': 'Annual fictional testing access',
        'contact_consent': True
    })
    assert result.status_code == 201, result.text
    admin = TestClient(app)
    nonadmin = TestClient(app)
    login(admin, 'practitioner@modelc.test')
    login(nonadmin, 'vikram@modelc.test')
    quotation_ids = [q['id'] for q in admin.get('/api/admin/organisation-quotes').json()]
    assert len(quotation_ids) == 1
    assert nonadmin.get('/api/admin/organisation-quotes').status_code == 403
    assert guest.get('/api/admin/organisation-quotes').status_code == 401
    reopened = create_app(database, init_database=True, seed_demo=False)
    newadmin = TestClient(reopened)
    login(newadmin, 'practitioner@modelc.test')
    assert [q['id'] for q in newadmin.get('/api/admin/organisation-quotes').json()] == quotation_ids
