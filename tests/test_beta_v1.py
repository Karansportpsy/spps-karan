"""Standalone beta v1.0 security, legacy-consent and free-release regression tests.
No real payment provider calls, patients or medical interpretations are used.
"""
from datetime import datetime,timezone,timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from test_support.app import create_app
from server.models import (Practitioner,PractitionerProfile,AppointmentSlot,Booking,PaymentAllocation,PractitionerSubscription,AssessmentAdministration,AssessmentSubscore,InjuryCheckIn,InjuryEpisode,RetentionHold,Athlete)
from server.security import make_password


def app_client(tmp_path):
    app=create_app(f"sqlite:///{tmp_path/'new_beta.db'}",init_database=True,seed_demo=True)
    return app,TestClient(app)

def auth(client,email='practitioner@modelc.test',pw='DemoOnly!2026'):
    r=client.post('/api/auth/login',json={'email':email,'password':pw,'terms_accepted':True})
    assert r.status_code==200,r.text
    return {'X-CSRF-Token':r.json()['csrf']}

def athlete_case(client,headers):
    r=client.post('/api/athletes',headers=headers,json={'code':'TEST-101','display_name':'Fictional Athlete','sport':'Badminton','service_consent_recorded':True})
    assert r.status_code==201,r.text
    aid=r.json()['id']
    r=client.post('/api/cases',headers=headers,json={'athlete_id':aid,'title':'Rehabilitation support'})
    assert r.status_code==201,r.text
    return aid,r.json()['id']


def test_directory_masking_and_tier(tmp_path,monkeypatch):
    monkeypatch.setenv('SPPS_BETA_V1_FREE','true')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','false')
    app,c=app_client(tmp_path)
    with app.state.DB() as db:
        subscriptions_before=db.scalar(select(func.count()).select_from(PractitionerSubscription))
    directory=c.get('/api/public/practitioners');assert directory.status_code==200
    body=directory.text.lower();profiles=directory.json()
    assert len(profiles)==6
    assert all(item['name'] and item['photo_url'].startswith('/api/public/practitioners/') for item in profiles)
    assert any(item['name']=='Mr. Vikram Sinha' for item in profiles) # Verified Free profiles are public.
    for bad in ['email','phone','password','linked_account','@modelc.test','demo practitioner']:
        assert bad not in body
    assert 'qualifications' in body and 'achievements' in body
    h=auth(c)
    t=c.get('/api/account/tier').json()
    assert t['tier']=='free' and t['prices_inr_per_month']=={}
    assert all(t['features'][name] for name in ('directory','bookings','video','report_drafts','report_exports'))
    assert t['features']['ai'] is False and t['test_payment_enabled'] is False
    slot={'starts_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),'duration_minutes':50,'amount_paise':0}
    assert c.post('/api/slots',headers=h,json=slot).status_code==201
    assert c.post('/api/slots',headers=h,json={**slot,'amount_paise':150000}).status_code==422
    aid,cid=athlete_case(c,h)
    draft=c.post('/api/reports',headers=h,json={'athlete_id':aid,'case_id':cid,'title':'Private report','body':'Personal and confidential progress notes'})
    assert draft.status_code==201,draft.text
    rid=draft.json()['id']
    # Report exports are free but must remain private to authenticated practitioners.
    assert TestClient(app).get(f'/api/reports/{rid}/export?format=pdf').status_code==401
    assert c.get(f'/api/reports/{rid}/export?format=pdf').status_code==200
    assert c.get(f'/api/athletes/{aid}/reports').status_code==200
    with app.state.DB() as db:
        user=db.scalar(select(Practitioner).where(Practitioner.email=='practitioner@modelc.test'))
        pid=user.id
    for legacy_plan in ('plus','pro'):
        retired=c.post(f'/api/admin/practitioners/{pid}/tier',
                       headers=h,json={'tier':legacy_plan})
        assert retired.status_code==410,retired.text
    assert c.post('/api/public/practitioner-registration/checkout',
                  json={'registration_token':'X'*40}).status_code==410
    with app.state.DB() as db:
        # Historical synthetic fixture subscriptions are preserved, but free
        # sign-in and rejected upgrades must not create any new subscription.
        assert db.scalar(select(func.count()).select_from(PractitionerSubscription))==subscriptions_before
    assert c.get('/api/account/tier').json()['features']['report_exports'] is True
    assert c.get('/api/account/tier').json()['features']['ai'] is False
    assert len(c.get('/api/public/practitioners').json())==6 # Entitlements never publish unverified profiles.


def test_booking_consent_payment_allocation_and_approval(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    app,c=app_client(tmp_path)
    h=auth(c)
    pr=c.get('/api/public/practitioners').json()[0]
    slots=c.get(f"/api/public/practitioners/{pr['id']}/slots").json();assert slots
    data={'slot_id':slots[0]['id'],'contact_name':'Synthetic Parent','contact_email':'payer@example.org','contact_consent':True,'is_minor':True,'guardian_confirmed':True,'video_consent':True}
    bad={**data,'guardian_confirmed':False}
    assert c.post('/api/public/bookings',json=bad).status_code==422
    r=c.post('/api/public/bookings',json=data);assert r.status_code==201,r.text
    bid=r.json()['booking_id']
    assert r.json()['status']=='awaiting_payment'
    assert c.post('/api/public/bookings',json=data).status_code==409
    assert c.post(f'/api/bookings/{bid}/approve',headers=h).status_code==409
    assert c.post(f'/api/internal/test-capture/{bid}').status_code==403
    v=c.post(f'/api/internal/test-capture/{bid}',headers=h);assert v.status_code==200,v.text
    assert v.json()['practitioner_paise']==105000
    assert v.json()['platform_paise']==45000
    assert v.json()['payout_status']=='allocation_only'
    again=c.post(f'/api/internal/test-capture/{bid}',headers=h)
    assert again.status_code==200
    with app.state.DB() as db:assert db.scalar(select(func.count()).select_from(PaymentAllocation))==1
    approved=c.post(f'/api/bookings/{bid}/approve',headers=h)
    assert approved.status_code==200
    assert approved.json()['room_id']
    link=c.post(f'/api/bookings/{bid}/room-invite',headers=h);assert link.status_code==200
    rid=link.json()['room_id'];token=link.json()['guest_token']
    assert c.get(f'/api/room/{rid}/status?access=nottherighttoken').status_code==403
    s=c.get(f'/api/room/{rid}/status?access={token}');assert s.status_code==200 and not s.json()['consented']
    assert s.json()['facial_emotion_analysis_enabled'] is False
    assert c.post(f'/api/room/{rid}/consent?access={token}',json={'accepted':False}).status_code==422
    assert c.post(f'/api/room/{rid}/consent?access={token}',json={'accepted':True}).status_code==200
    r=c.post(f'/api/rooms/{rid}/reviews',headers=h,json={'time_offset_seconds':62,'kind':'practitioner_observation','note':'Client explicitly described higher confidence.'})
    assert r.status_code==201
    assert TestClient(app).get(f'/api/rooms/{rid}/reviews').status_code==401
    assert len(c.get(f'/api/rooms/{rid}/reviews',headers=h).json())==1


def test_assessment_subfactor_units_and_scope(tmp_path):
    app,c=app_client(tmp_path);h=auth(c);aid,cid=athlete_case(c,h)
    data={'athlete_id':aid,'case_id':cid,'instrument_name':'Mock offline assessment','instrument_version':'v1','observed_on':'2026-11-04','scores':[{'parent_factor':'Anxiety','sub_factor':'Cognitive','unit':'Raw Score','value':17},{'parent_factor':'Anxiety','sub_factor':'Somatic','unit':'Percentile','value':77},{'parent_factor':'Confidence','sub_factor':'Perceived readiness','unit':'Z-Score','value':-0.5},{'parent_factor':'Attention','sub_factor':'Focus','unit':'Sten','value':5}]}
    r=c.post('/api/assessments/offline',headers=h,json=data);assert r.status_code==201,r.text
    assert r.json()['score_count']==4
    item=c.get(f'/api/athletes/{aid}/assessments/offline').json()[0]
    assert len(item['scores'])==4
    assert item['instrument_version']=='v1'
    bad=data.copy();bad['scores']=[{'parent_factor':'Anxiety','sub_factor':'Somatic','unit':'Percentile','value':120}]
    assert c.post('/api/assessments/offline',headers=h,json=bad).status_code==422
    with app.state.DB() as db:
        p=db.scalar(select(Practitioner).where(Practitioner.email=='practitioner@modelc.test'))
        p2=Practitioner(tenant_id=p.tenant_id,email='different@same.test',name='Other Practitioner',password_hash=make_password('Another!2026'));db.add(p2);db.flush();from datetime import timedelta,timezone
        db.add(PractitionerSubscription(practitioner_id=p2.id,plan='core',amount_paise=19900,status='active',test_mode=True,current_period_start=datetime.now(timezone.utc),current_period_end=datetime.now(timezone.utc)+timedelta(days=30)));db.commit()
    c.post('/api/auth/logout',headers=h);auth(c,'different@same.test','Another!2026')
    assert c.get(f'/api/athletes/{aid}/assessments/offline').status_code==404


def test_injury_and_indices(tmp_path):
    app,c=app_client(tmp_path);h=auth(c);aid,cid=athlete_case(c,h)
    r=c.post('/api/injury/episodes',headers=h,json={'athlete_id':aid,'case_id':cid,'injury_label':'Fictional ACL rehabilitation','stage':'early rehabilitation','start_date':'2026-11-01'});assert r.status_code==201
    ep=r.json()['id']
    data={'observed_on':'2026-11-04','pain':3,'fear_of_reinjury':7,'confidence':5,'coping':6,'note':'Synthetic observations'}
    assert c.post(f'/api/injury/episodes/{ep}/checkins',headers=h,json=data).status_code==201
    assert c.post(f'/api/injury/episodes/{ep}/checkins',headers=h,json={**data,'pain':11}).status_code==422
    assert len(c.get(f'/api/athletes/{aid}/injury').json()[0]['checkins'])==1
    factors=c.get('/api/factors').json();stress=next(f for f in factors if f['label']=='Stress')
    assert c.post('/api/observations',headers=h,json={'athlete_id':aid,'case_id':cid,'factor_id':stress['id'],'value':8,'observed_on':'2026-11-04'}).status_code==201
    result=c.get(f'/api/athletes/{aid}/indices');assert result.status_code==200
    row=result.json()['series'][0];assert row['load_index'] is not None and row['recovery_index'] is not None
    assert 'not a validated' in result.json()['method'].lower()


def test_local_test_data_erasure_routes_are_not_shipped(tmp_path,monkeypatch):
    # SPPS Version 1.0 - Free ships no test-data tooling: the local erase/purge shortcuts were removed.
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_ERASURE','true')
    app,c=app_client(tmp_path);h=auth(c);aid,cid=athlete_case(c,h)
    assert c.post(f'/api/athletes/{aid}/erase',headers=h,json={'confirmation':f'DELETE {aid}'}).status_code in (404,405)
    assert c.post('/api/admin/purge',headers=h,json={'confirmation':'PURGE ALL TEST DATA'}).status_code in (404,405)
    assert c.get(f'/api/athletes/{aid}',headers=h).status_code==200


def test_login_affirmative_acknowledgement(tmp_path):
    app,c=app_client(tmp_path)
    no=c.post('/api/auth/login',json={'email':'practitioner@modelc.test','password':'DemoOnly!2026'})
    assert no.status_code==422
    h=auth(c)
    assert c.get('/api/account/tier').status_code==200


def test_private_export_pdf_csv_and_contact_redaction(tmp_path,monkeypatch):
    monkeypatch.setenv('SPPS_BETA_V1_FREE','true')
    app,c=app_client(tmp_path);h=auth(c);aid,cid=athlete_case(c,h)
    r=c.post('/api/reports',headers=h,json={'athlete_id':aid,'case_id':cid,'title':'Synthetic progress report','audience':'practitioner','body':'Only practitioner-authored synthetic material.\nNo clinical assessment shared.'})
    assert r.status_code==201,r.text
    rid=r.json()['id']
    # Free export still requires a practitioner session and never exposes private drafts to visitors.
    assert TestClient(app).get(f'/api/reports/{rid}/export?format=pdf').status_code==401
    pdf=c.get(f'/api/reports/{rid}/export?format=pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    assert 'private' not in pdf.headers.get('cache-control','') or pdf.headers['cache-control']=='no-store'
    csv=c.get(f'/api/reports/{rid}/export?format=csv')
    assert csv.status_code==200 and b'Synthetic progress report' in csv.content
    assert TestClient(app).get(f'/api/reports/{rid}/export?format=pdf').status_code==401
    with app.state.DB() as db:
        profile=db.scalar(select(PractitionerProfile))
        profile.qualifications='MSc, call 9876543210 and write admin@sample.test'
        profile.achievements='Visit https://sample.test and @sample'
        db.commit()
    text=c.get('/api/public/practitioners').text
    assert '9876543210' not in text
    assert '@sample.test' not in text
    assert 'https://sample.test' not in text


def test_free_ai_not_misrepresented_as_active(tmp_path,monkeypatch):
    monkeypatch.setenv('SPPS_BETA_V1_FREE','true')
    app,c=app_client(tmp_path);auth(c)
    tier=c.get('/api/account/tier').json()
    assert tier['tier']=='free'
    assert tier['prices_inr_per_month']=={}
    assert tier['features']['ai'] is False
    assert tier['features']['video'] is True
    assert tier['features']['report_exports'] is True
