"""Stability patch contract: only fictional synthetic data and disposable DBs."""
from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from test_support.app import create_app

def login(client,email):
    r=client.post('/api/auth/login',json={'email':email,'password':'DemoOnly!2026','terms_accepted':True})
    assert r.status_code==200,r.text
    return {'X-CSRF-Token':r.json()['csrf']}

def test_practitioner_slots_are_private_and_refresh_after_bookings(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    app=create_app(f'sqlite:///{tmp_path / "fictional-slots.db"}',seed_demo=True)
    practitioner=TestClient(app);other=TestClient(app);guest=TestClient(app)
    p=login(practitioner,'practitioner@modelc.test')
    login(other,'vikram@modelc.test')
    original=practitioner.get('/api/slots')
    assert original.status_code==200,original.text
    assert other.get('/api/slots').status_code==200
    starts_at=(datetime.now(timezone.utc)+timedelta(days=4)).isoformat()
    resp=practitioner.post('/api/slots',headers=p,json={'starts_at':starts_at,'duration_minutes':45,'amount_paise':190000})
    assert resp.status_code==201,resp.text
    sid=resp.json()['id']
    own=practitioner.get('/api/slots').json()
    assert any(r['id']==sid and r['status']=='available' for r in own)
    assert not any(r['id']==sid for r in other.get('/api/slots').json())
    assert guest.get('/api/slots').status_code==401
    booking=guest.post('/api/public/bookings',json={'slot_id':sid,'contact_name':'Synthetic Sample Guest','contact_email':'synthetic-guest@example.org','contact_consent':True,'video_consent':True})
    assert booking.status_code==201,booking.text
    assert next(r for r in practitioner.get('/api/slots').json() if r['id']==sid)['status']=='reserved'
    assert all('contact_email' not in r for r in practitioner.get('/api/slots').json())

def test_slot_is_persisted_after_backend_reopen(tmp_path):
    url=f'sqlite:///{tmp_path / "fictional-persist.db"}'
    a=create_app(url,seed_demo=True)
    web=TestClient(a);h=login(web,'practitioner@modelc.test')
    created=web.post('/api/slots',headers=h,json={'starts_at':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat(),'duration_minutes':50,'amount_paise':150000})
    assert created.status_code==201,created.text
    b=create_app(url,init_database=True,seed_demo=False)
    mobile=TestClient(b);login(mobile,'practitioner@modelc.test')
    assert any(s['id']==created.json()['id'] for s in mobile.get('/api/slots').json())
