"""Milestone 1.1 fictional roundtrip acceptance on one disposable database.

This is a backend confidence check, NOT a physical Android acceptance test.
"""
from fastapi.testclient import TestClient
from test_support.app import create_app


def authenticate(client, email):
    response = client.post('/api/auth/login', json={
        'email': email, 'password': 'DemoOnly!2026', 'terms_accepted': True
    })
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def test_both_directions_assessment_monitoring_restart_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV', 'local')
    url = f"sqlite:///{tmp_path / 'fictional-m11.sqlite3'}"
    app = create_app(url, init_database=True, seed_demo=True)
    desktop, android, outsider = TestClient(app), TestClient(app), TestClient(app)
    authenticate(desktop, 'practitioner@modelc.test')
    authenticate(android, 'practitioner@modelc.test')
    authenticate(outsider, 'vikram@modelc.test')

    athlete = desktop.post('/api/athletes', json={
        'code': 'M11-FICTIONAL', 'display_name': 'Fictional M11 athlete',
        'sport': 'Badminton', 'service_consent_recorded': True,
    })
    assert athlete.status_code == 201, athlete.text
    athlete_id = athlete.json()['id']
    case = desktop.post('/api/cases', json={'athlete_id': athlete_id, 'title': 'Fictional M11 case'})
    assert case.status_code == 201, case.text
    case_id = case.json()['id']
    factor_id = next(item['id'] for item in desktop.get('/api/factors').json() if item['kind'] == 'monitoring')

    for client, score in [(android, 4), (desktop, 8)]:
        assessment = client.post('/api/assessments/offline', json={
            'athlete_id': athlete_id, 'case_id': case_id, 'instrument_name': 'Fictional M11 scale',
            'instrument_version': 'v1', 'observed_on': '2026-11-09',
            'scores': [{'parent_factor': 'Focus', 'sub_factor': 'Attention', 'unit': 'Raw Score', 'value': score}],
        })
        assert assessment.status_code == 201, assessment.text
        observed = client.post('/api/observations', json={
            'athlete_id': athlete_id, 'case_id': case_id, 'factor_id': factor_id,
            'observed_on': '2026-11-09', 'value': score, 'context': 'Fictional test entry',
        })
        assert observed.status_code == 201, observed.text
        for other in (desktop, android):
            runs = other.get(f'/api/athletes/{athlete_id}/assessments/offline')
            values = other.get(f'/api/athletes/{athlete_id}/observations')
            assert runs.status_code == 200
            assert values.status_code == 200
            assert runs.headers['Cache-Control'] == 'no-store'
            assert values.headers['Cache-Control'] == 'no-store'
            assert assessment.json()['id'] in [row['id'] for row in runs.json()]
            assert observed.json()['id'] in [row['id'] for row in values.json()]

    assert outsider.get(f'/api/athletes/{athlete_id}/assessments/offline').status_code == 404
    assert outsider.get(f'/api/athletes/{athlete_id}/observations').status_code == 404
    assert outsider.post('/api/assessments/offline', json={
        'athlete_id': athlete_id, 'case_id': case_id, 'instrument_name': 'Unpermitted',
        'observed_on': '2026-11-09', 'scores': [{
            'parent_factor': 'Focus', 'sub_factor': 'Attention', 'unit': 'Raw Score', 'value': 5,
        }],
    }).status_code == 404

    # Recreate the app while preserving the disposable database file.
    restarted = create_app(url, init_database=True, seed_demo=False)
    fresh = TestClient(restarted)
    authenticate(fresh, 'practitioner@modelc.test')
    assert len(fresh.get(f'/api/athletes/{athlete_id}/assessments/offline').json()) == 2
    assert len(fresh.get(f'/api/athletes/{athlete_id}/observations').json()) == 2

