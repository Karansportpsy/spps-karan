"""Registration/billing isolation and 70:30 ledger — synthetic local only."""
import base64,io,json,hmac,hashlib
from datetime import timedelta
from PIL import Image
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from test_support.app import create_app
from server.models import Practitioner, PractitionerSubscription, PractitionerProfile, PaymentAllocation, RegistrationIntent, SubscriptionCharge
from server.subscription_routes import PLANS
from server.models import now

def avatar():
    b=io.BytesIO();Image.new('RGB',(180,180),(26,122,143)).save(b,format='PNG');return 'data:image/png;base64,'+base64.b64encode(b.getvalue()).decode()

def reg(plan='core',email=None):
    return {'name':'Dr. Synthetic Registration','email':email or f'new-{plan}@example.org',
            'password':'AComplexDemoPass!2026','qualifications':'MSc Sport Psychology (synthetic)',
            'experience_years':7,'achievements':'Synthetic training work','specialisms':'Confidence, attention',
            'consultation_fee_paise':180000,'photo_base64':avatar(),'plan':plan,'terms_accepted':True}

def client(tmp_path):
    app=create_app(f"sqlite:///{tmp_path/'new_registration.db'}",init_database=True,seed_demo=True)
    return app, TestClient(app)


def approve_synthetic_application(app, practitioner_id, admin_client, headers):
    """Test fixture: complete the *owner* approval path after synthetic email verification.

    Paid membership, credential verification and directory publication alone
    must never bypass the new independent pending/approved account gate.
    """
    with app.state.DB() as db:
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==practitioner_id))
        assert profile is not None
        profile.email_verified_at=now()  # fictional verified-email fixture
        profile.supabase_auth_uid=f"00000000-0000-4000-8000-{practitioner_id:012d}"
        db.commit()
    response=admin_client.post(
        f'/api/admin/practitioners/{practitioner_id}/approval',
        json={'decision':'approve','note':'Independent synthetic account approval'},
        headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['status']=='approved' and response.json()['listed'] is True
    return response


def test_pricing_and_unpaid_invisibility(tmp_path):
    app,c=client(tmp_path)
    plans=c.get('/api/public/practitioner-plans').json()
    assert {p['id']:p['amount_paise'] for p in plans['plans']}==PLANS
    old=len(c.get('/api/public/practitioners').json())
    result=c.post('/api/public/practitioner-registration',json=reg())
    assert result.status_code==201,result.text
    token=result.json()['registration_token']
    pending=c.post('/api/public/practitioner-registration/status',json={'registration_token':token})
    assert pending.status_code==200 and pending.json()['status']=='pending'
    assert result.json()['amount_paise']==19900
    assert c.get('/api/public/practitioners').json().__len__()==old
    fail=c.post('/api/auth/login',json={'email':'new-core@example.org','password':'AComplexDemoPass!2026','terms_accepted':True})
    assert fail.status_code==403
    assert c.post('/api/public/practitioner-registration',json=reg()).status_code==409
    assert c.post('/api/public/practitioner-registration/checkout',json={'registration_token':token}).status_code==503
    assert c.post('/api/internal/test-activate-subscription',json={'registration_token':token}).status_code==404
    with app.state.DB() as db:
        row=db.scalar(select(Practitioner).where(Practitioner.email=='new-core@example.org'))
        assert row and not row.is_admin
        pr=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==row.id))
        assert pr and pr.avatar_bytes.startswith(b'RIFF') and not pr.public_approved
        sub=db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id==row.id))
        assert sub.status=='pending' and sub.amount_paise==19900

def test_local_activation_and_expiry(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local');monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION','true')
    app,c=client(tmp_path)
    old=len(c.get('/api/public/practitioners').json())
    data=c.post('/api/public/practitioner-registration',json=reg()).json()
    tok=data['registration_token']
    answer=c.post('/api/internal/test-activate-subscription',json={'registration_token':tok})
    assert answer.status_code==200 and answer.json()['test_mode'] and not answer.json()['charged_real_money']
    assert c.post('/api/public/practitioner-registration/status',json={'registration_token':tok}).json()['status']=='active'
    assert c.post('/api/internal/test-activate-subscription',json={'registration_token':tok}).json()['already_active']
    # Stage 13D contract: an active paid membership alone never publishes an unverified profile.
    assert len(c.get('/api/public/practitioners').json())==old
    status=c.post('/api/public/practitioner-registration/status',json={'registration_token':tok}).json()
    assert status['directory_visible'] is False and status['credentials_verified'] is False
    admin=c.post('/api/auth/login',json={'email':'practitioner@modelc.test','password':'DemoOnly!2026','terms_accepted':True}).json()
    h={'X-CSRF-Token':admin['csrf']}
    with app.state.DB() as db:
        new_id=db.scalar(select(Practitioner.id).where(Practitioner.email=='new-core@example.org'))
    assert c.post(f'/api/admin/practitioners/{new_id}/publication',json={'decision':'approve','reason':'Fictional publication'},headers=h).status_code==409
    assert c.post(f'/api/admin/practitioners/{new_id}/credentials',json={'decision':'verify','reason':'Fictional registry check'},headers=h).status_code==200
    assert len(c.get('/api/public/practitioners').json())==old  # verified but not yet approved for publication
    assert c.post(f'/api/admin/practitioners/{new_id}/publication',json={'decision':'approve','reason':'Fictional publication'},headers=h).status_code==200
    # Review/publication decisions alone do not activate the new account gate.
    assert len(c.get('/api/public/practitioners').json())==old
    approve_synthetic_application(app,new_id,c,h)
    c.post('/api/auth/logout',headers=h)
    profiles=c.get('/api/public/practitioners').json()
    assert len(profiles)==old+1
    pr=next(x for x in profiles if x['name']=='Dr. Synthetic Registration')
    assert pr['consultation_fee_paise']==180000 and pr['credentials_verified']
    assert c.get(pr['photo_url']).status_code==200
    assert 'image/webp' in c.get(pr['photo_url']).headers['content-type']
    assert '@example.org' not in str(profiles)
    login=c.post('/api/auth/login',json={'email':'new-core@example.org','password':'AComplexDemoPass!2026','terms_accepted':True})
    assert login.status_code==200,login.text
    assert c.get('/api/account/tier').json()['features']['report_exports'] is False
    with app.state.DB() as db:
        sub=db.scalar(select(PractitionerSubscription).join(Practitioner,Practitioner.id==PractitionerSubscription.practitioner_id).where(Practitioner.email=='new-core@example.org'))
        sub.current_period_end=now()-timedelta(seconds=1);db.commit()
    assert c.get('/api/account/tier').status_code==403
    assert len(c.get('/api/public/practitioners').json())==old
    assert c.get(pr['photo_url']).status_code==404


def test_pro_plan_export_permission_and_simulation(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local');monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true');monkeypatch.setenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION','true')
    app,c=client(tmp_path)
    result=c.post('/api/public/practitioner-registration',json=reg('pro'))
    assert result.status_code==201
    tok=result.json()['registration_token']
    assert result.json()['amount_paise']==49900
    assert c.post('/api/internal/test-activate-subscription',json={'registration_token':tok}).status_code==200
    assert c.post('/api/auth/login',json={'email':'new-pro@example.org','password':'AComplexDemoPass!2026','terms_accepted':True}).status_code==200
    tier=c.get('/api/account/tier').json()
    assert tier['tier']=='pro' and tier['features']['report_exports'] is True
    assert tier['features']['ai'] is False
    with app.state.DB() as db:
        sub=db.scalar(select(PractitionerSubscription).join(Practitioner,PractitionerSubscription.practitioner_id==Practitioner.id).where(Practitioner.email=='new-pro@example.org'))
        assert sub.amount_paise==49900 and sub.test_mode
        assert db.scalar(select(func.count()).select_from(SubscriptionCharge).where(SubscriptionCharge.subscription_id==sub.id))==1


def test_fixed_commission_and_cancelled_subscription(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_COMMISSION_BPS','100')  # must not override the fixed 30% rule
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    app,c=client(tmp_path)
    login=c.post('/api/auth/login',json={'email':'practitioner@modelc.test','password':'DemoOnly!2026','terms_accepted':True})
    h={'X-CSRF-Token':login.json()['csrf']}
    pro=c.get('/api/public/practitioners').json()[0]
    slot=c.get(f"/api/public/practitioners/{pro['id']}/slots").json()[0]
    paid=c.post('/api/public/bookings',json={'slot_id':slot['id'],'contact_name':'Synthetic Customer','contact_email':'parent@example.org','contact_consent':True})
    assert paid.status_code==201
    bid=paid.json()['booking_id']
    with app.state.DB() as db:
        from server.models import Booking
        b=db.get(Booking,bid);assert b.commission_bps==3000
    capture=c.post(f'/api/internal/test-capture/{bid}',headers=h)
    assert capture.status_code==200
    assert capture.json()['practitioner_paise']==slot['amount_paise']*7//10
    assert capture.json()['platform_paise']==slot['amount_paise']*3//10
    assert capture.json()['payout_status']=='allocation_only'
    again=c.post(f'/api/internal/test-capture/{bid}',headers=h)
    assert again.status_code==200
    with app.state.DB() as db:
        assert db.scalar(select(func.count()).select_from(PaymentAllocation))==1


def test_invalid_registration_and_webhook_signature(tmp_path,monkeypatch):
    app,c=client(tmp_path)
    a=reg();a['plan']='free';assert c.post('/api/public/practitioner-registration',json=a).status_code==422
    a=reg();a['terms_accepted']=False;assert c.post('/api/public/practitioner-registration',json=a).status_code==422
    a=reg();a['photo_base64']='not-an-image'*3;assert c.post('/api/public/practitioner-registration',json=a).status_code==422
    monkeypatch.setenv('RAZORPAY_WEBHOOK_SECRET','fake-webhook-secret')
    wh=c.post('/api/payment/razorpay/subscription-webhook',content=b'{"event":"subscription.activated"}',headers={'x-razorpay-signature':'bad'})
    assert wh.status_code==403


def test_paid_practitioner_isolated_from_other_factors(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    monkeypatch.setenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION','true')
    app,c=client(tmp_path)
    signed=c.post('/api/auth/login',json={'email':'practitioner@modelc.test',
        'password':'DemoOnly!2026','terms_accepted':True})
    assert signed.status_code==200,signed.text
    csrf={'X-CSRF-Token':signed.json()['csrf']}
    added=c.post('/api/factors',headers=csrf,json={
        'label':'Other clinician private test factor','group_name':'Private',
        'kind':'monitoring','unit':'points','low':0,'high':10,'higher_is':'contextual'})
    assert added.status_code==201,added.text
    services=c.get('/api/public/services').json()['services']
    assert services
    registration=c.post('/api/public/practitioner-registration',json=reg('core','isolated@example.org')).json()
    assert c.post('/api/internal/test-activate-subscription',json={
        'registration_token':registration['registration_token']}).status_code==200
    login=c.post('/api/auth/login',json={
        'email':'isolated@example.org','password':'AComplexDemoPass!2026','terms_accepted':True})
    assert login.status_code==200,login.text
    own_csrf={'X-CSRF-Token':login.json()['csrf']}
    dashboard=c.get('/api/dashboard').json()
    assert 'new_enquiries' not in dashboard['stats'] and 'enquiries' not in dashboard
    factors=c.get('/api/factors').json()
    assert 'Other clinician private test factor' not in {x['label'] for x in factors}
