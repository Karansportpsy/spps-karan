"""Directory and synthetic demo seed regression tests: no real athlete data."""
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from test_support.app import create_app
from server.models import (Practitioner,PractitionerProfile,Athlete,Factor,Observation,
                           AssessmentAdministration,AssessmentSubscore,InjuryEpisode,InjuryCheckIn)


def app_and_client(tmp_path):
    path=tmp_path/'directory_test.db'
    app=create_app(f'sqlite:///{path}',init_database=True,seed_demo=True)
    return path,app,TestClient(app)


def test_seeded_fictional_profiles_and_public_contact_masking(tmp_path):
    _path,app,c=app_and_client(tmp_path)
    public=c.get('/api/public/practitioners')
    assert public.status_code==200
    profiles=public.json()
    assert len(profiles)==6
    assert len({x['name'] for x in profiles})==6
    assert all(x['name'] and x['qualifications'] and x['achievements'] for x in profiles)
    assert all(x['experience_years']>0 for x in profiles)
    for p in profiles:
        assert p['photo_url']==f"/api/public/practitioners/{p['id']}/photo", 'fixture photos are served as uploads'
        resp=c.get(p['photo_url'])
        assert resp.status_code==200 and resp.headers.get('content-type','').startswith('image/webp')
        assert 'email' not in p and 'phone' not in p and 'linked_account_id' not in p
        assert '@' not in str(p) and 'tel:' not in str(p)
    with app.state.DB() as db:
        assert db.scalar(select(func.count()).select_from(Practitioner))==6
        tiers=db.scalars(select(Practitioner.tier)).all()
        assert tiers.count('core')==4 and tiers.count('plus')==0 and tiers.count('pro')==2
        free=db.scalar(select(Practitioner).where(Practitioner.email=='vikram@modelc.test'))
        assert free.name=='Mr. Vikram Sinha'
        assert free.id in {x['id'] for x in profiles}
    assert c.get('/practitioners').status_code==200
    assert c.get('/').status_code==200


def test_contact_redaction_with_untrusted_profile_copy(tmp_path):
    _path,app,c=app_and_client(tmp_path)
    with app.state.DB() as db:
        p=db.scalar(select(Practitioner).where(Practitioner.email=='practitioner@modelc.test'))
        p.name='Dr. Test Person (+91 98765 43210)'
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==p.id))
        profile.qualifications='PhD; reach dr.test@example.org; +91 98765 43210'
        profile.achievements='Write to dr.test@example.org or +91 98765 43210'
        profile.avatar_key='http://danger.test/unsafe.png'  # Must NEVER be reflected as a public URL.
        profile.avatar_bytes=None  # no uploaded photo: the stored key must not become a URL
        db.commit()
    result=c.get('/api/public/practitioners').json()
    first=next(x for x in result if x['id']==p.id)
    assert '@' not in str(first) and '98765' not in str(first)
    assert 'danger.test' not in str(first)
    assert first['photo_url']=='/assets/avatars/placeholder.svg', 'an unrecognised key falls back to the neutral placeholder'


def test_synthetic_athlete_indices_assessments_injuries_and_seed_idempotence(tmp_path):
    path,app,c=app_and_client(tmp_path)
    with app.state.DB() as db:
        initial_counts={
            'athletes':db.scalar(select(func.count()).select_from(Athlete)),
            'factors':db.scalar(select(func.count()).select_from(Factor)),
            'observations':db.scalar(select(func.count()).select_from(Observation)),
            'assessments':db.scalar(select(func.count()).select_from(AssessmentAdministration)),
            'subscores':db.scalar(select(func.count()).select_from(AssessmentSubscore)),
            'injuries':db.scalar(select(func.count()).select_from(InjuryEpisode)),
            'rehab':db.scalar(select(func.count()).select_from(InjuryCheckIn)),
        }
        assert initial_counts['athletes']==9
        assert initial_counts['factors']>=10
        assert initial_counts['observations']>=9*21*10
        assert initial_counts['assessments']>=9*2
        assert initial_counts['subscores']>=9*2*5
        assert initial_counts['injuries']>=3
        assert initial_counts['rehab']>=3*5
    auth=c.post('/api/auth/login',json={'email':'practitioner@modelc.test','password':'DemoOnly!2026','terms_accepted':True})
    assert auth.status_code==200
    athletes=c.get('/api/athletes').json()
    assert len(athletes)>=6
    selected=next(a for a in athletes if a['code']=='MC-001')
    indices=c.get(f"/api/athletes/{selected['id']}/indices")
    assert indices.status_code==200
    assert len(indices.json()['series'])>=21
    assert all('load_index' in row and 'recovery_index' in row for row in indices.json()['series'])
    assert 'not a validated' in indices.json()['method'].lower()
    runs=c.get(f"/api/athletes/{selected['id']}/assessments/offline").json()
    assert len(runs)>=2
    assert len(runs[0]['scores'])>=5
    assert {'Raw Score','Percentile','Z-Score','T-Score','Sten'}.issubset({s['unit'] for run in runs for s in run['scores']})
    injured=next(a for a in athletes if a['code']=='MC-002')
    injury=c.get(f"/api/athletes/{injured['id']}/injury").json()
    assert injury and len(injury[0]['checkins'])>=5
    # Calling the same demo seed on existing database doesn't duplicate historical fixtures.
    second=create_app(f'sqlite:///{path}',init_database=True,seed_demo=True)
    with second.state.DB() as db:
        assert {key:db.scalar(select(func.count()).select_from(model)) for key,model in [
            ('athletes',Athlete),('factors',Factor),('observations',Observation),
            ('assessments',AssessmentAdministration),('subscores',AssessmentSubscore),
            ('injuries',InjuryEpisode),('rehab',InjuryCheckIn)]}==initial_counts


def test_free_practitioner_bookable_and_has_core_workspace(tmp_path):
    _path,app,c=app_and_client(tmp_path)
    public=c.get('/api/public/practitioners').json()
    with app.state.DB() as db:
        free=db.scalar(select(Practitioner).where(Practitioner.email=='vikram@modelc.test'))
        fid=free.id
    assert fid in [item['id'] for item in public]
    assert c.get(f'/api/public/practitioners/{fid}/slots').status_code==200
    r=c.post('/api/auth/login',json={'email':'vikram@modelc.test','password':'DemoOnly!2026','terms_accepted':True})
    assert r.status_code==200
    assert c.get('/api/account/tier').json()['tier']=='core'
    assert c.get('/api/athletes').json()[0]['code']=='MC-009'
    assert c.get('/api/bookings').status_code==200


def test_existing_local_beta_profile_avatar_migration(tmp_path):
    """Simulate an older Beta v1.0 SQLite profile lacking the new avatar_key."""
    from sqlalchemy import inspect,text
    from server.migrate_local import migrate_local
    _path,app,_client=app_and_client(tmp_path)
    with app.state.engine.begin() as conn:
        conn.execute(text('ALTER TABLE practitioner_profiles DROP COLUMN avatar_key'))
    assert 'avatar_key' not in [x['name'] for x in inspect(app.state.engine).get_columns('practitioner_profiles')]
    changed=migrate_local(app.state.engine)
    assert changed==['practitioner_profiles.avatar_key']
    assert 'avatar_key' in [x['name'] for x in inspect(app.state.engine).get_columns('practitioner_profiles')]
    assert migrate_local(app.state.engine)==[]
