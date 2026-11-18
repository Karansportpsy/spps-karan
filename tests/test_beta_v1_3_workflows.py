"""Cross-client, persisted fictional workflows for Beta v1.3.

These tests emulate Windows and Android as independent HTTP clients sharing one
FastAPI database; they are *not* a substitute for live phone UI acceptance.
No real person, billing provider or external service is used.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from test_support.app import create_app
from server.models import Booking, PaymentAllocation


def login(client, email='practitioner@modelc.test'):
    response = client.post('/api/auth/login', json={
        'email': email,
        'password': 'DemoOnly!2026',
        'terms_accepted': True,
    })
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': response.json()['csrf']}


def test_windows_and_android_share_persisted_practitioner_records(tmp_path):
    db_url = f'sqlite:///{tmp_path / "shared-fictional.db"}'
    app = create_app(db_url, init_database=True, seed_demo=True)
    windows = TestClient(app)
    android = TestClient(app)
    wcsrf = login(windows)
    acsrf = login(android)

    new_athlete = windows.post('/api/athletes', headers=wcsrf, json={
        'code': 'B13-SYNTH', 'display_name': 'Synthetic Beta 13 Athlete',
        'sport': 'Badminton', 'service_consent_recorded': True,
    })
    assert new_athlete.status_code == 201, new_athlete.text
    athlete_id = new_athlete.json()['id']
    assert any(a['id'] == athlete_id for a in android.get('/api/athletes').json())

    case = android.post('/api/cases', headers=acsrf, json={
        'athlete_id': athlete_id, 'title': 'Synthetic competition preparation',
    })
    assert case.status_code == 201, case.text
    case_id = case.json()['id']
    assert any(c['id'] == case_id for c in windows.get(f'/api/athletes/{athlete_id}').json()['cases'])

    note = windows.post('/api/notes', headers=wcsrf, json={
        'case_id': case_id, 'note_date': '2026-11-05',
        'content': 'Fictional task-focus routine and recovery cue agreed.',
    })
    assert note.status_code == 201, note.text
    assert any(n['id'] == note.json()['id'] for n in android.get(f'/api/cases/{case_id}').json()['notes'])

    factor = android.post('/api/factors', headers=acsrf, json={
        'label': 'Synthetic recovery readiness', 'low': 0, 'high': 10,
        'kind': 'monitoring', 'higher_is': 'higher-supportive',
    })
    assert factor.status_code == 201, factor.text
    observation = windows.post('/api/observations', headers=wcsrf, json={
        'athlete_id': athlete_id, 'case_id': case_id,
        'factor_id': factor.json()['id'], 'value': 7,
        'observed_on': '2026-11-05',
    })
    assert observation.status_code == 201, observation.text
    assert any(o['value'] == 7 for o in android.get(f'/api/athletes/{athlete_id}/observations').json())

    # Reopen the same SQLite file in a new app instance (simulates a backend restart).
    restarted = create_app(db_url, init_database=True, seed_demo=False)
    fresh_client = TestClient(restarted)
    login(fresh_client)
    assert any(a['id'] == athlete_id for a in fresh_client.get('/api/athletes').json())
    assert fresh_client.get(f'/api/cases/{case_id}').json()['notes'][0]['content'].startswith('Fictional')
    assert len(fresh_client.get(f'/api/athletes/{athlete_id}/observations').json()) == 1

    # Another practitioner may not read the private record from the shared backend.
    another = TestClient(restarted)
    login(another, 'vikram@modelc.test')
    assert another.get(f'/api/athletes/{athlete_id}').status_code == 404
    assert another.get(f'/api/cases/{case_id}').status_code == 404


def test_booking_ledger_is_single_record_across_clients(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE', 'true')
    app = create_app(f'sqlite:///{tmp_path / "booking-fictional.db"}', seed_demo=True)
    windows, android = TestClient(app), TestClient(app)
    wcsrf, acsrf = login(windows), login(android)
    slot = windows.post('/api/slots', headers=wcsrf, json={
        'starts_at': (datetime.now(timezone.utc)+timedelta(days=3)).isoformat(),
        'duration_minutes': 50, 'amount_paise': 150000,
    })
    assert slot.status_code == 201, slot.text
    booking = android.post('/api/public/bookings', json={
        'slot_id': slot.json()['id'], 'contact_name': 'Fictional Booking Guest',
        'contact_email': 'guest-fictional@example.org',
        'contact_consent': True, 'video_consent': True,
    })
    assert booking.status_code == 201, booking.text
    bid = booking.json()['booking_id']
    assert android.post('/api/public/bookings', json={
        'slot_id': slot.json()['id'], 'contact_name': 'Other Fictional Guest',
        'contact_email': 'other-fictional@example.org', 'contact_consent': True,
    }).status_code == 409

    capture = windows.post(f'/api/internal/test-capture/{bid}', headers=wcsrf)
    assert capture.status_code == 200, capture.text
    assert capture.json()['practitioner_paise'] == 105000
    assert capture.json()['platform_paise'] == 45000
    assert android.post(f'/api/internal/test-capture/{bid}', headers=acsrf).status_code == 200
    assert android.post(f'/api/bookings/{bid}/approve', headers=acsrf).status_code == 200

    with app.state.DB() as db:
        bookings = db.scalars(select(Booking).where(Booking.id == bid)).all()
        allocation = db.scalars(select(PaymentAllocation).where(PaymentAllocation.booking_id == bid)).all()
        assert len(bookings) == 1
        assert len(allocation) == 1
        assert allocation[0].payout_status == 'allocation_only'  # No payout occurred.
