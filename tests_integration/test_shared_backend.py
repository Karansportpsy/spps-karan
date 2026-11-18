"""Real PostgreSQL, distinct cookie jars, actual restricted login roles."""
from uuid import uuid4
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, select
from sqlalchemy.exc import DBAPIError
from server.identity import bind_identity, require_local_database
from server.models import Practitioner, Athlete


def login(client, env, name='alice'):
    response = client.post('/api/auth/login', json={
        'email': f'{name}@spps.test', 'password': env[f'SPPS_DEV_{name.upper()}_PASSWORD'],
        'terms_accepted': True})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    return response.json()['id']


def create_athlete(client, code=None):
    response = client.post('/api/athletes', json={'code': code or uuid4().hex,
        'display_name': 'Fictional Integration Athlete', 'sport': 'Badminton',
        'service_consent_recorded': True})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_two_clients_roundtrip_restart_and_practitioner_isolation(app, integration_env):
    with TestClient(app) as web, TestClient(app) as android, TestClient(app) as outsider:
        login(web, integration_env)
        login(android, integration_env)
        login(outsider, integration_env, 'bob')
        aid = create_athlete(web)
        assert android.get(f'/api/athletes/{aid}').status_code == 200
        response = android.post('/api/cases', json={'athlete_id': aid, 'title': 'Fictional case'})
        assert response.status_code == 201, response.text
        case = response.json()['id']
        factor = web.get('/api/factors').json()[0]['id']
        for client, value in ((web, 6), (android, 8)):
            observation = {'athlete_id': aid, 'case_id': case, 'factor_id': factor,
                           'value': value, 'observed_on': '2026-11-09', 'context': 'Fictional only'}
            assert client.post('/api/observations', json=observation).status_code == 201
            assessment = {'athlete_id': aid, 'case_id': case, 'instrument_name': 'Fictional scale',
                          'observed_on': '2026-11-09', 'scores': [{'parent_factor': 'Focus',
                          'sub_factor': 'Attention', 'unit': 'Raw Score', 'value': value}]}
            result = client.post('/api/assessments/offline', json=assessment)
            assert result.status_code == 201, result.text
        for client in (web, android):
            assert len(client.get(f'/api/athletes/{aid}/observations').json()) == 2
            assert len(client.get(f'/api/athletes/{aid}/assessments/offline').json()) == 2
        for suffix in ('', '/observations', '/assessments/offline', '/reports', '/indices', '/injury'):
            assert outsider.get(f'/api/athletes/{aid}{suffix}').status_code == 404
        assert outsider.post('/api/observations', json=observation).status_code == 404
        assert outsider.post('/api/assessments/offline', json=assessment).status_code == 404
        assert outsider.get(f'/api/cases/{case}').status_code == 404
        assert aid not in [row['id'] for row in outsider.get('/api/athletes').json()]
        # Closing pools/recreating the app models process lifetime. Browser test
        # additionally terminates and restarts an actual uvicorn subprocess.
        saved = dict(web.cookies)
    app.state.engine.dispose()
    app.state.auth_engine.dispose()
    from test_support.app import create_app
    restarted = create_app(init_database=False, seed_demo=False)
    try:
        with TestClient(restarted) as client:
            client.cookies.update(saved)
            assert client.get('/api/auth/me').status_code == 200
            assert len(client.get(f'/api/athletes/{aid}/observations').json()) == 2
            assert len(client.get(f'/api/athletes/{aid}/assessments/offline').json()) == 2
    finally:
        restarted.state.engine.dispose()
        restarted.state.auth_engine.dispose()


def test_authentication_csrf_revocation_and_closed_commerce(app, integration_env):
    with TestClient(app) as client:
        assert client.get('/api/athletes').status_code == 401
        assert client.post('/api/auth/login', json={'email': 'alice@spps.test', 'password': 'invalid',
                                                   'terms_accepted': True}).status_code == 401
        login(client, integration_env)
        csrf = client.headers.pop('X-CSRF-Token')
        assert client.post('/api/athletes', json={'code': 'DENIED', 'display_name': 'Fictional',
                                                'sport': 'Tennis'}).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        cookie = dict(client.cookies)
        assert client.post('/api/auth/logout').status_code == 200
        client.cookies.update(cookie)
        assert client.get('/api/athletes').status_code == 401
        assert client.post('/api/public/practitioner-registration', json={}).status_code == 503
        assert client.post('/api/payment/razorpay/webhook', json={}).status_code == 503


def identities(app):
    with app.state.AuthDB() as db:
        return [SimpleNamespace(id=u.id, tenant_id=u.tenant_id) for u in
                db.scalars(select(Practitioner).where(Practitioner.is_admin == False)  # noqa: E712
                           .order_by(Practitioner.email))]


def test_database_default_deny_and_connection_pool_identity_reset(app, integration_env):
    alice, bob = identities(app)
    with TestClient(app) as web:
        login(web, integration_env)
        aid = create_athlete(web)
    with app.state.DB() as db:
        assert db.scalars(select(Athlete)).all() == []
        bind_identity(db, alice)
        assert db.get(Athlete, aid) is not None
        db.commit()
        assert db.scalar(select(Athlete.id).where(Athlete.id == aid)) == aid
        db.rollback()
        assert db.scalar(select(Athlete.id).where(Athlete.id == aid)) == aid
    for identity in (bob, None, alice, bob, None):
        with app.state.DB() as db:
            if identity:
                bind_identity(db, identity)
            found = db.scalar(select(Athlete.id).where(Athlete.id == aid))
            assert found == (aid if identity is alice else None)
    with app.state.DB() as db:
        bind_identity(db, SimpleNamespace(id=alice.id, tenant_id=alice.tenant_id+1000))
        assert db.scalars(select(Athlete)).all() == []


@pytest.mark.parametrize('query', [
    'SELECT * FROM practitioners', 'SELECT * FROM login_sessions',
    'SELECT * FROM practitioner_subscriptions', 'SELECT * FROM room_access',
    'SET ROLE spps_dev_auth', 'ALTER TABLE athletes DISABLE ROW LEVEL SECURITY',
    'DELETE FROM athletes', 'UPDATE athletes SET owner_practitioner_id=2',
])
def test_clinical_role_cannot_access_auth_or_escalate(app, query):
    with app.state.DB() as db, pytest.raises(DBAPIError):
        db.execute(text(query))


def test_auth_role_has_no_clinical_access(app):
    with app.state.AuthDB() as db, pytest.raises(DBAPIError):
        db.execute(text('SELECT * FROM athletes'))


def test_rls_rejects_forged_ownership_and_nested_relationships(app, integration_env):
    alice, bob = identities(app)
    with TestClient(app) as web, TestClient(app) as other:
        login(web, integration_env)
        login(other, integration_env, 'bob')
        own = create_athlete(web)
        foreign = create_athlete(other)
        foreign_case = other.post('/api/cases', json={'athlete_id': foreign, 'title': 'Foreign fictional case'}).json()['id']
        own_case = web.post('/api/cases', json={'athlete_id': own, 'title': 'Own fictional case'}).json()['id']
        foreign_factor = other.post('/api/factors', json={'label': 'Private fictional factor'}).json()['id']
        factor = web.get('/api/factors').json()[0]['id']
        foreign_run = other.post('/api/assessments/offline', json={'athlete_id': foreign,
            'case_id': foreign_case, 'instrument_name': 'Foreign fictional scale', 'observed_on': '2026-11-09',
            'scores': [{'parent_factor': 'Focus', 'sub_factor': 'Attention', 'unit': 'Raw Score', 'value': 5}]}).json()['id']
    queries = [
        ("INSERT INTO athletes (tenant_id,owner_practitioner_id,code,display_name,sport,athlete_group,active,created_at) VALUES (:tid,:bob,'FORGED','Fictional','Tennis','test',true,now())", {}),
        ("INSERT INTO observations (tenant_id,athlete_id,case_id,factor_id,value,observed_on,source,context,created_at) VALUES (:tid,:aid,:cid,:fid,5,'2026-11-09','test','fictional',now())", {'aid': foreign, 'cid': foreign_case, 'fid': factor}),
        ("INSERT INTO observations (tenant_id,athlete_id,case_id,factor_id,value,observed_on,source,context,created_at) VALUES (:tid,:aid,:cid,:fid,5,'2026-11-09','test','fictional',now())", {'aid': own, 'cid': foreign_case, 'fid': factor}),
        ("INSERT INTO observations (tenant_id,athlete_id,case_id,factor_id,value,observed_on,source,context,created_at) VALUES (:tid,:aid,:cid,:fid,5,'2026-11-09','test','fictional',now())", {'aid': own, 'cid': own_case, 'fid': foreign_factor}),
        ("INSERT INTO assessment_subscores (administration_id,parent_factor,sub_factor,unit,value,source) VALUES (:run,'Focus','Attention','Raw Score',5,'test')", {'run': foreign_run}),
    ]
    for query, params in queries:
        with app.state.DB() as db:
            bind_identity(db, alice)
            with pytest.raises(DBAPIError) as failure:
                db.execute(text(query), {'tid': alice.tenant_id, 'bob': bob.id, **params})
            assert failure.value.orig.sqlstate == '42501'
    with app.state.DB() as db:
        bind_identity(db, alice)
        assert db.scalar(text('SELECT count(*) FROM assessment_subscores WHERE administration_id=:id'), {'id': foreign_run}) == 0
        assert db.execute(text('UPDATE athletes SET service_consent_recorded_at=now() WHERE id=:id'), {'id': foreign}).rowcount == 0


@pytest.mark.parametrize('url', ['postgresql://u:p@db.example.com/spps_dev',
    'postgresql://u:p@localhost/postgres', 'postgresql://u:p@127.0.0.1/spps_dev?options=-csearch_path=public',
    'sqlite:///spps_dev.db'])
def test_development_refuses_hosted_or_nonisolated_targets(url):
    with pytest.raises(RuntimeError):
        require_local_database(url)
