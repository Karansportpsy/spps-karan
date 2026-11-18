"""Free v1 report exports are practitioner-specific (SQLite smoke test only)."""
from fastapi.testclient import TestClient
from test_support.app import create_app

def test_free_draft_exports_and_cross_practitioner_denial(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    monkeypatch.setenv('SPPS_BETA_V1_FREE','true')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','false')
    app=create_app('sqlite:///'+str(tmp_path/'test.db'),init_database=True,seed_demo=True)
    alice=TestClient(app)
    login=alice.post('/api/auth/login',json={'email':'practitioner@modelc.test','password':'DemoOnly!2026','terms_accepted':True})
    assert login.status_code==200
    h={'X-CSRF-Token':login.json()['csrf']}
    athlete=alice.post('/api/athletes',json={'code':'FREE-REPORT-001','display_name':'Fictional Athlete','sport':'Football','service_consent_recorded':True},headers=h)
    assert athlete.status_code==201,athlete.text
    aid=athlete.json()['id']
    case=alice.post('/api/cases',json={'athlete_id':aid,'title':'Fictional Stage One Case','category':'Competition','goal':'Fictional confidence monitoring'},headers=h)
    assert case.status_code==201,case.text
    cid=case.json()['id']
    report=alice.post('/api/reports',json={'athlete_id':aid,'case_id':cid,'title':'Fictional Confidential Draft','audience':'practitioner','body':'Fictional data only; this draft must never be shared automatically.'},headers=h)
    assert report.status_code==201,report.text
    rid=report.json()['id']
    pdf=alice.get(f'/api/reports/{rid}/export?format=pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
    assert pdf.headers['cache-control']=='no-store'
    csv=alice.get(f'/api/reports/{rid}/export?format=csv')
    assert csv.status_code==200 and b'Fictional Confidential Draft' in csv.content
    bob=TestClient(app)
    assert bob.post('/api/auth/login',json={'email':'rohan@modelc.test','password':'DemoOnly!2026','terms_accepted':True}).status_code==200
    assert bob.get(f'/api/reports/{rid}/export?format=pdf').status_code==404
