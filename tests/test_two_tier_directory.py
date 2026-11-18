"""Standalone Model C two-tier/free-plan and directory-home tests (synthetic data only)."""
from datetime import datetime,timezone,timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select,text
from test_support.app import create_app
from server.models import Practitioner,PractitionerProfile


def boot(tmp_path):
    app=create_app(f"sqlite:///{tmp_path/'two_tier.db'}",seed_demo=True)
    return app,TestClient(app)

def login(client,email='vikram@modelc.test'):
    result=client.post('/api/auth/login',json={'email':email,'password':'DemoOnly!2026','terms_accepted':True})
    assert result.status_code==200,result.text
    return {'X-CSRF-Token':result.json()['csrf']}

def test_directory_home_free_plus_pro_and_contact_boundaries(tmp_path):
    app,c=boot(tmp_path)
    assert c.get('/').status_code==200
    assert c.get('/practitioners').status_code==200
    js=c.get('/assets/app.js').text
    assert 'practitioner-register' in js and 'directoryPage' in js
    profiles=c.get('/api/public/practitioners').json()
    assert len(profiles)==6
    with app.state.DB() as db:
        plans=set(db.scalars(select(Practitioner.tier)).all())
        assert plans=={'core','pro'}
        free=db.scalar(select(Practitioner).where(Practitioner.email=='vikram@modelc.test'))
        assert free.id in {p['id'] for p in profiles}
    for p in profiles:
        assert set(p)=={'id','name','qualifications','experience_years','achievements','specialisms','photo_url','consultation_fee_paise','credentials_verified','featured_owner','role'}
        # The additional featured/role fields are public presentation metadata,
        # never login email, Supabase identity or private clinical information.
        assert p['featured_owner'] is False and p['role']==''
        assert '@' not in str(p) and 'phone' not in str(p).lower()
        assert c.get(f"/api/public/practitioners/{p['id']}/slots").status_code==200

def test_core_has_unlimited_core_and_only_pro_exports(tmp_path):
    app,c=boot(tmp_path);h=login(c)
    tier=c.get('/api/account/tier').json()
    assert tier['tier']=='core'
    assert all(tier['features'][x] for x in ['athletes','directory','bookings','video','reports','report_drafts'])
    assert not tier['features']['report_exports'] and not tier['features']['ai']
    for i in range(12):
        r=c.post('/api/athletes',headers=h,json={'code':f'NEW-{i:03d}','display_name':f'Synthetic {i}','sport':'Football','service_consent_recorded':True})
        assert r.status_code==201,r.text
    for i in range(12):
        r=c.post('/api/factors',headers=h,json={'label':f'Factor {i}','group_name':'Custom check-in','low':0,'high':10})
        assert r.status_code==201,r.text
    user_id=app.state.DB().execute(select(Practitioner.id).where(Practitioner.email=='vikram@modelc.test')).scalar_one()
    # Validate admin privilege boundary: a regular Free practitioner cannot alter tier.
    assert c.post(f'/api/admin/practitioners/{user_id}/tier',headers=h,json={'tier':'pro'}).status_code==403
    assert c.post('/api/slots',headers=h,json={'starts_at':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat(),'duration_minutes':50,'amount_paise':120000}).status_code==201
    assert c.get('/api/bookings').status_code==200

def test_legacy_plus_local_upgrade_to_core(tmp_path):
    from server.migrate_local import migrate_local
    app,c=boot(tmp_path)
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE practitioners SET tier='plus' WHERE email='rohan@modelc.test'"))
    changes=migrate_local(app.state.engine)
    assert changes==[]
    with app.state.DB() as db:
        assert db.scalar(select(Practitioner.tier).where(Practitioner.email=='rohan@modelc.test'))=='core'
    assert len(c.get('/api/public/practitioners').json())==6

def test_profile_approval_required_independent_of_plan(tmp_path):
    app,c=boot(tmp_path)
    with app.state.DB() as db:
        person=db.scalar(select(Practitioner).where(Practitioner.email=='vikram@modelc.test'))
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==person.id))
        profile.public_approved=False;pid=person.id;db.commit()
    assert len(c.get('/api/public/practitioners').json())==5
    assert c.get(f'/api/public/practitioners/{pid}/slots').status_code==404
