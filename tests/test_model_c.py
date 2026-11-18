import os, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support.app import create_app
from server.models import Tenant,Practitioner,PractitionerSubscription,Service,Case,Athlete,LoginSession,Inquiry,Observation,Factor,Report
from server.security import make_password


def setup_app(tmp_path):
    app=create_app(f"sqlite:///{tmp_path/'model_c_test.db'}",init_database=True,seed_demo=True)
    c=TestClient(app)
    return app,c

def login(c,email='practitioner@modelc.test',pw='DemoOnly!2026'):
    r=c.post('/api/auth/login',json={'email':email,'password':pw,'terms_accepted':True})
    assert r.status_code==200,r.text
    return {'X-CSRF-Token':r.json()['csrf']}

def enquiry_payload(service_id):
    return {'service_id':service_id,'contact_name':'Sample Parent','contact_email':'parent@example.org','contact_phone':'','enquiring_for':'my child / dependent','sport':'Badminton','support_topic':'Confidence','contact_preference':'email','scheduling_note':'Weekend afternoons','consent':True}

def test_public_service_and_enquiry(tmp_path):
    _,c=setup_app(tmp_path)
    services=c.get('/api/public/services');assert len(services.json()['services'])==4
    sid=services.json()['services'][0]['id']
    req=c.post('/api/public/enquiries',json=enquiry_payload(sid));assert req.status_code==201
    assert req.json()['accepted'] is True
    assert c.get('/api/enquiries').status_code==401
    bad=enquiry_payload(sid);bad['consent']=False
    assert c.post('/api/public/enquiries',json=bad).status_code==422

def test_practitioner_enquiry_to_case(tmp_path):
    _,c=setup_app(tmp_path)
    sid=c.get('/api/public/services').json()['services'][0]['id']
    c.post('/api/public/enquiries',json=enquiry_payload(sid))
    headers=login(c)
    enquires=c.get('/api/enquiries').json();assert len(enquires)==1
    eid=enquires[0]['id']
    assert c.post(f'/api/enquiries/{eid}/convert',headers=headers,json={'athlete_code':'MC-888','athlete_display_name':'Demo Youth','consent_checked':False}).status_code==422
    converted=c.post(f'/api/enquiries/{eid}/convert',headers=headers,json={'athlete_code':'MC-888','athlete_display_name':'Demo Youth','consent_checked':True});assert converted.status_code==201
    athlete_id=converted.json()['athlete_id']
    newcase=c.post('/api/cases',headers=headers,json={'athlete_id':athlete_id,'title':'Pre-match routine','category':'Performance psychology','goal':'Regulate attention'});assert newcase.status_code==201
    case_id=newcase.json()['id']
    note=c.post('/api/notes',headers=headers,json={'case_id':case_id,'note_date':'2026-11-04','content':'Discussed task-relevant focus and agreed a reset cue.'});assert note.status_code==201
    assert c.post(f"/api/notes/{note.json()['id']}/finalize",headers=headers,json={'expected_revision':1}).status_code==200
    assert c.post(f"/api/notes/{note.json()['id']}/finalize",headers=headers,json={'expected_revision':1}).status_code==409
    aid=c.post('/api/factors',headers=headers,json={'label':'Attention recovery','group_name':'Competition','kind':'assessment','low':0,'high':10,'unit':'points','higher_is':'higher-supportive'}).json()['id']
    assert c.post('/api/observations',headers=headers,json={'athlete_id':athlete_id,'case_id':case_id,'factor_id':aid,'value':12,'observed_on':'2026-11-04'}).status_code==422
    result=c.post('/api/observations',headers=headers,json={'athlete_id':athlete_id,'case_id':case_id,'factor_id':aid,'value':7,'observed_on':'2026-11-04'});assert result.status_code==201
    obs=c.get(f'/api/athletes/{athlete_id}/observations').json();assert len(obs)==1 and obs[0]['label']=='Attention recovery'
    evidence=c.get('/api/interventions').json();assert len(evidence)>=3
    assign=c.post('/api/assignments',headers=headers,json={'athlete_id':athlete_id,'case_id':case_id,'intervention_id':evidence[0]['id'],'dosage':'5 min twice per week'});assert assign.status_code==201
    report=c.post('/api/reports',headers=headers,json={'athlete_id':athlete_id,'case_id':case_id,'title':'Progress note','audience':'coach','body':'Approved sharing not yet implemented; private draft only.'});assert report.status_code==201
    assert report.json()['status']=='draft'

def test_auth_csrf_and_tenant_isolation(tmp_path):
    app,c=setup_app(tmp_path)
    h=login(c)
    assert c.post('/api/athletes',json={'code':'A44','display_name':'Private Athlete','sport':'Football'}).status_code==403
    with app.state.DB() as db:
        t=Tenant(slug='other-team',display_name='Other Organisation');db.add(t);db.flush()
        u=Practitioner(tenant_id=t.id,email='someone@other.test',name='Other Person',password_hash=make_password('Another!2026'));db.add(u);db.flush();from datetime import datetime,timezone,timedelta
        db.add(PractitionerSubscription(practitioner_id=u.id,plan='core',amount_paise=19900,status='active',test_mode=True,current_period_start=datetime.now(timezone.utc),current_period_end=datetime.now(timezone.utc)+timedelta(days=30)));db.commit()
    r=c.post('/api/athletes',headers=h,json={'code':'A44','display_name':'Private Athlete','sport':'Football','service_consent_recorded':False});assert r.status_code==201
    aid=r.json()['id']
    refused=c.post('/api/cases',headers=h,json={'athlete_id':aid,'title':'New case'});assert refused.status_code==403
    assert c.post(f'/api/athletes/{aid}/service-consent',headers=h,json={'confirmed':True}).status_code==200
    assert c.post('/api/cases',headers=h,json={'athlete_id':aid,'title':'New case'}).status_code==201
    c.post('/api/auth/logout',headers=h)
    h2=login(c,'someone@other.test','Another!2026')
    assert c.get(f'/api/athletes/{aid}').status_code==404
    assert c.get('/api/athletes').json()==[]
    assert c.get('/api/enquiries').json()==[]
    assert c.post('/api/observations',headers=h2,json={'athlete_id':aid,'factor_id':1,'value':5,'observed_on':'2026-11-04'}).status_code==404

def test_public_inputs_and_headers(tmp_path):
    _,c=setup_app(tmp_path)
    services=c.get('/api/public/services');assert services.headers['content-security-policy'].startswith("default-src 'self'")
    sid=services.json()['services'][0]['id']
    bad=enquiry_payload(sid);bad['support_topic']='Detailed psychiatric history'
    assert c.post('/api/public/enquiries',json=bad).status_code==422
    honeypot=enquiry_payload(sid);honeypot['website']='bot.example'
    assert c.post('/api/public/enquiries',json=honeypot).status_code==201
    h=login(c)
    assert c.get('/api/dashboard').status_code==200
    assert c.post('/api/auth/logout',headers=h).status_code==200
    assert c.get('/api/dashboard').status_code==401
