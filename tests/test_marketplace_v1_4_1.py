"""Beta 1.4.1 marketplace validation with fictional identities and disposable DB."""
from fastapi.testclient import TestClient
from test_support.app import create_app


def sign_in(client, email):
    response=client.post('/api/auth/login',json={
        'email':email,'password':'DemoOnly!2026','terms_accepted':True})
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':response.json()['csrf']}


def test_practitioner_programmes_and_private_requests(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    dburl=f'sqlite:///{tmp_path / "programme.sqlite3"}'
    app=create_app(dburl,seed_demo=True)
    owner=TestClient(app);outsider=TestClient(app);guest=TestClient(app)
    h=sign_in(owner,'practitioner@modelc.test')
    other=sign_in(outsider,'vikram@modelc.test')
    create=owner.post('/api/practitioner/programmes',headers=h,json={
        'title':'Fictional four-week focus training',
        'duration_weeks':4,'sessions_included':4,'amount_paise':600000,
        'deliverables':'Four supervised video sessions and a fictional athlete practice worksheet.'
    })
    assert create.status_code==201,create.text
    programme=create.json()
    assert programme['published'] is True
    pid=programme['id']
    assert any(p['id']==pid for p in guest.get('/api/public/programmes').json())
    assert not any(p['id']==pid for p in outsider.get('/api/practitioner/programmes').json())
    assert outsider.patch(f'/api/practitioner/programmes/{pid}',headers=other,json={'status':'hidden'}).status_code==404
    invalid=guest.post('/api/public/programme-enquiries',json={
        'programme_id':pid,'contact_name':'Fictional Parent','contact_email':'parent@example.org',
        'contact_consent':True,'is_minor':True,'guardian_confirmed':False})
    assert invalid.status_code==422
    requested=guest.post('/api/public/programme-enquiries',json={
        'programme_id':pid,'contact_name':'Fictional Parent','contact_email':'parent@example.org',
        'contact_consent':True,'is_minor':True,'guardian_confirmed':True})
    assert requested.status_code==201,requested.text
    assert requested.json()['payment_status']=='not_charged'
    records=owner.get('/api/practitioner/programme-enquiries').json()
    assert len(records)==1 and records[0]['quoted_amount_paise']==600000
    assert not outsider.get('/api/practitioner/programme-enquiries').json()
    assert outsider.patch(f'/api/practitioner/programme-enquiries/{records[0]["id"]}',headers=other,json={'status':'contacted'}).status_code==404
    updated=owner.patch(f'/api/practitioner/programme-enquiries/{records[0]["id"]}',headers=h,json={'status':'contacted'})
    assert updated.status_code==200 and updated.json()['status']=='contacted'
    hidden=owner.patch(f'/api/practitioner/programmes/{pid}',headers=h,json={'status':'hidden'})
    assert hidden.status_code==200 and hidden.json()['published'] is False
    assert not any(p['id']==pid for p in guest.get('/api/public/programmes').json())
    assert guest.post('/api/public/programme-enquiries',json={
        'programme_id':pid,'contact_name':'Fictional Other','contact_email':'other@example.org','contact_consent':True}).status_code==404
    reopened=create_app(dburl,seed_demo=False)
    reread=TestClient(reopened);sign_in(reread,'practitioner@modelc.test')
    assert reread.get('/api/practitioner/programme-enquiries').json()[0]['status']=='contacted'
    assert reread.get('/api/practitioner/programmes').json()[0]['published'] is False


def test_organisation_quotes_are_admin_only(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_C_ENV','local')
    app=create_app(f'sqlite:///{tmp_path / "quote.sqlite3"}',seed_demo=True)
    visitor=TestClient(app);admin=TestClient(app);other=TestClient(app)
    sign_in(admin,'practitioner@modelc.test')
    sign_in(other,'vikram@modelc.test')
    assert other.get('/api/admin/organisation-quotes').status_code==403
    assert visitor.post('/api/public/organisation-quote',json={
        'organisation_name':'Imaginary National Sports Agency',
        'contact_name':'Example Contact','contact_email':'contact@example.org',
        'practitioner_seats':7,'athletes_estimate':280,
        'requirements':'Role-separated records and annual procurement support',
        'contact_consent':False}).status_code==422
    result=visitor.post('/api/public/organisation-quote',json={
        'organisation_name':'Imaginary National Sports Agency',
        'contact_name':'Example Contact','contact_email':'contact@example.org',
        'practitioner_seats':7,'athletes_estimate':280,
        'requirements':'Role-separated records and annual procurement support',
        'contact_consent':True})
    assert result.status_code==201,result.text
    assert result.json()['reference'].startswith('ORG-')
    found=admin.get('/api/admin/organisation-quotes')
    assert found.status_code==200
    assert len(found.json())==1
    assert found.json()[0]['practitioner_seats']==7
    assert other.get('/api/admin/organisation-quotes').status_code==403
    assert visitor.get('/api/admin/organisation-quotes').status_code==401
