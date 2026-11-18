"""Beta 1.4 end-to-end fictional workflows. Uses only disposable databases."""
import os
from datetime import datetime, timedelta, timezone, date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support.app import create_app
from server.models import Booking, PaymentAllocation, ConsultationRoom, RoomAccess


def sign_in(client, email='practitioner@modelc.test'):
    r=client.post('/api/auth/login', json={'email':email,'password':'DemoOnly!2026','terms_accepted':True})
    assert r.status_code==200, r.text
    return {'X-CSRF-Token':r.json()['csrf']}


def test_fictional_booking_to_completed_consultation_and_revoke(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    monkeypatch.setenv('MODEL_C_WEBRTC_ENABLED','true')
    url=f'sqlite:///{tmp_path / "complete-workflow.db"}'
    app=create_app(url,init_database=True,seed_demo=True)
    assert TestClient(app).get('/health/live').json()=={'status':'ok','version':'1.0.0'}
    web=TestClient(app); phone=TestClient(app); outsider=TestClient(app)
    wc=sign_in(web); pc=sign_in(phone); oc=sign_in(outsider,'vikram@modelc.test')
    offer=web.post('/api/slots',headers=wc,json={
        'starts_at':(datetime.now(timezone.utc)+timedelta(days=4)).isoformat(),
        'duration_minutes':50,'amount_paise':150000,
    })
    assert offer.status_code==201,offer.text
    sid=offer.json()['id']
    # A second practitioner cannot see another user's private appointment slots.
    assert not any(s['id']==sid for s in outsider.get('/api/slots').json())
    booking=phone.post('/api/public/bookings',json={
        'slot_id':sid,'contact_name':'Fictional Consultation Guest',
        'contact_email':'fictional-consultation@example.org',
        'contact_consent':True,'video_consent':True,
    })
    assert booking.status_code==201,booking.text
    bid=booking.json()['booking_id']
    assert web.post(f'/api/bookings/{bid}/approve',headers=wc).status_code==409
    assert outsider.post(f'/api/bookings/{bid}/complete',headers=oc).status_code==404
    capture=web.post(f'/api/internal/test-capture/{bid}',headers=wc)
    assert capture.status_code==200,capture.text
    assert capture.json()['practitioner_paise']==105000
    assert capture.json()['platform_paise']==45000
    assert phone.post(f'/api/internal/test-capture/{bid}',headers=pc).status_code==200
    approval=phone.post(f'/api/bookings/{bid}/approve',headers=pc)
    assert approval.status_code==200,approval.text
    room_id=approval.json()['room_id']
    invitation=web.post(f'/api/bookings/{bid}/room-invite',headers=wc)
    assert invitation.status_code==200,invitation.text
    guest_token=invitation.json()['guest_token']
    practitioner_token=invitation.json()['practitioner_token']
    for token in (guest_token,practitioner_token):
        consent=phone.post(f'/api/room/{room_id}/consent?access={token}',json={'accepted':True})
        assert consent.status_code==200,consent.text
    # Role authorization, room note privacy, and explicit completion are independent.
    note=web.post(f'/api/rooms/{room_id}/reviews',headers=wc,json={
        'time_offset_seconds':100,'kind':'practitioner_observation',
        'note':'Fictional discussion of an agreed attention-reset routine.'})
    assert note.status_code==201,note.text
    assert outsider.get(f'/api/rooms/{room_id}/reviews').status_code==404
    assert outsider.post(f'/api/bookings/{bid}/complete',headers=oc).status_code==404
    # Prevent unauthenticated callers from finalizing sessions.
    assert TestClient(app).post(f'/api/bookings/{bid}/complete').status_code==401
    finished=phone.post(f'/api/bookings/{bid}/complete',headers=pc)
    assert finished.status_code==200,finished.text
    assert finished.json()['invite_tokens_revoked'] is True
    assert finished.json()['room_active'] is False
    assert next(b for b in web.get('/api/bookings').json() if b['id']==bid)['status']=='completed'
    assert web.get(f'/api/room/{room_id}/status',params={'access':guest_token}).status_code==403
    assert web.get(f'/api/room/{room_id}/status',params={'access':practitioner_token}).status_code==403
    assert web.post(f'/api/bookings/{bid}/room-invite',headers=wc).status_code==403
    assert web.post(f'/api/bookings/{bid}/complete',headers=wc).status_code==409
    assert phone.post('/api/public/bookings',json={
        'slot_id':sid,'contact_name':'Second Fictional Guest',
        'contact_email':'fictional-second@example.org','contact_consent':True,
    }).status_code==409  # Completed slots must never be sold again.
    with app.state.DB() as db:
        b=db.get(Booking,bid)
        room=db.get(ConsultationRoom,room_id)
        assert b.status=='completed' and room.active is False
        assert all(a.revoked for a in db.scalars(select(RoomAccess).where(RoomAccess.room_id==room_id)))
        allocations=db.scalars(select(PaymentAllocation).where(PaymentAllocation.booking_id==bid)).all()
        assert len(allocations)==1
        assert allocations[0].payout_status=='allocation_only'
    restarted=create_app(url,init_database=True,seed_demo=False)
    fresh=TestClient(restarted);sign_in(fresh)
    assert next(b for b in fresh.get('/api/bookings').json() if b['id']==bid)['status']=='completed'
    assert fresh.get(f'/api/room/{room_id}/status',params={'access':guest_token}).status_code==403


def test_clinical_workflows_scoped_and_consent_gated(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    app=create_app(f'sqlite:///{tmp_path / "clinical.db"}',seed_demo=True)
    clinician=TestClient(app);other=TestClient(app)
    h=sign_in(clinician)
    sign_in(other,'vikram@modelc.test')
    athlete=clinician.post('/api/athletes',headers=h,json={
        'code':'B14-FICT','display_name':'Fictional Athlete Fourteen',
        'sport':'Athletics','service_consent_recorded':False,
    })
    assert athlete.status_code==201,athlete.text
    aid=athlete.json()['id']
    case_data={'athlete_id':aid,'title':'Synthetic return-to-sport readiness'}
    assert clinician.post('/api/cases',headers=h,json=case_data).status_code==403
    assert clinician.post(f'/api/athletes/{aid}/service-consent',headers=h,json={'confirmed':True}).status_code==200
    case=clinician.post('/api/cases',headers=h,json=case_data)
    assert case.status_code==201,case.text
    cid=case.json()['id']
    nowday=date.today().isoformat()
    assessment=clinician.post('/api/assessments/offline',headers=h,json={
        'athlete_id':aid,'case_id':cid,'instrument_name':'Fictional readiness inventory',
        'observed_on':nowday,'scores':[{'parent_factor':'Confidence','sub_factor':'Imagery confidence','unit':'Raw Score','value':6}],
    })
    assert assessment.status_code==201,assessment.text
    factor=clinician.post('/api/factors',headers=h,json={'label':'Fictional daily readiness','low':0,'high':10})
    assert factor.status_code==201,factor.text
    observation=clinician.post('/api/observations',headers=h,json={
        'athlete_id':aid,'case_id':cid,'factor_id':factor.json()['id'],
        'observed_on':nowday,'value':7,
    })
    assert observation.status_code==201,observation.text
    injury=clinician.post('/api/injury/episodes',headers=h,json={
        'athlete_id':aid,'case_id':cid,'injury_label':'Fictional ankle rehabilitation',
        'start_date':nowday,'stage':'mid rehabilitation',
    })
    assert injury.status_code==201,injury.text
    checkin=clinician.post(f'/api/injury/episodes/{injury.json()["id"]}/checkins',headers=h,json={
        'observed_on':nowday,'pain':1,'fear_of_reinjury':4,'confidence':7,'coping':8,
    })
    assert checkin.status_code==201,checkin.text
    report=clinician.post('/api/reports',headers=h,json={
        'athlete_id':aid,'case_id':cid,'title':'Fictional readiness summary',
        'audience':'practitioner','body':'This entirely fictional case uses practitioner-entered monitoring data only.',
    })
    assert report.status_code==201,report.text
    assert clinician.get(f'/api/athletes/{aid}/assessments/offline').status_code==200
    assert clinician.get(f'/api/athletes/{aid}/injury').status_code==200
    assert len(clinician.get(f'/api/athletes/{aid}/reports').json())==1
    # Core tier may draft but cannot export.
    assert clinician.get(f'/api/reports/{report.json()["id"]}/export').status_code==403
    assert other.get(f'/api/athletes/{aid}').status_code==404
    assert other.get(f'/api/athletes/{aid}/injury').status_code==404
    assert other.get(f'/api/athletes/{aid}/assessments/offline').status_code==404
    assert other.get(f'/api/athletes/{aid}/reports').status_code==404
    assert other.get(f'/api/athletes/{aid}/observations').status_code==404
    assert other.post('/api/assessments/offline',headers={'X-CSRF-Token':'invalid'},json={}).status_code in (403,422)


def test_hosted_mode_rejects_demo_insecure_cookies_and_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','staging')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','false')
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_ERASURE','false')
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION','false')
    monkeypatch.setenv('MODEL_C_COOKIE_SECURE','false')
    monkeypatch.setenv('DATABASE_URL','postgresql://example.test/db')
    with pytest.raises(RuntimeError,match='demo seed'):
        create_app(init_database=False,seed_demo=True)
    with pytest.raises(RuntimeError,match='secure session cookies'):
        create_app(init_database=False,seed_demo=False)
    monkeypatch.setenv('MODEL_C_COOKIE_SECURE','true')
    with pytest.raises(RuntimeError,match='managed PostgreSQL'):
        create_app(f'sqlite:///{tmp_path / "must-not-create.db"}',init_database=False,seed_demo=False)
    assert not (tmp_path/'must-not-create.db').exists()
