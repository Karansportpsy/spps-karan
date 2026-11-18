"""Model C: separate public enquiry gateway + private practitioner workspace.
Public visitors do not receive clinical records or practitioner credentials.
"""
from __future__ import annotations
import os, re, time, secrets, threading, mimetypes
from pathlib import Path
from datetime import date
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Literal
from fastapi import FastAPI, Depends, Request, Response, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator
from sqlalchemy import select, func, delete, or_, text
from sqlalchemy.orm import Session
from .db import make_engine,make_session,Base
from .models import (Tenant,Practitioner,LoginSession,Service,Athlete,Case,Note,
                     Factor,Observation,Intervention,Assignment,Report,Audit,TermsAcceptance,now)
from .security import verify_password,hash_token,new_token,expiry,is_expired
from .subscription_routes import active_membership,register_subscription_routes,listing_eligible
from . import free_access
from . import supabase_practitioner_auth as sbauth
from .models import PractitionerProfile
from .development_profile import allowed as development_route_allowed

# Windows registry MIME associations vary; packaged portraits are always WebP.
mimetypes.add_type('image/webp', '.webp')

ROOT=Path(__file__).resolve().parent.parent
WEB=ROOT/'web'
LIMITS=defaultdict(deque)
from .security import make_password as _make_password
DUMMY_PASSWORD_HASH=_make_password(secrets.token_urlsafe(24))
import json as _json, logging as _logging, uuid as _uuid
REQUEST_LOG=_logging.getLogger('spps.request')
LIMIT_LOCK=threading.Lock()

class LoginIn(BaseModel):
    email:str=Field(min_length=4,max_length=250)
    password:str=Field(min_length=1,max_length=200)
    terms_accepted:bool=False
class SupabaseExchangeIn(BaseModel):
    access_token:str=Field(min_length=100,max_length=8192)
    terms_accepted:bool=False
class AthleteIn(BaseModel):
    code:str=Field(min_length=2,max_length=32,pattern=r'^[A-Za-z0-9_-]+$')
    display_name:str=Field(min_length=2,max_length=120)
    sport:str=Field(min_length=2,max_length=80)
    athlete_group:str=Field(default='not specified',max_length=40)
    service_consent_recorded:bool=False
class CaseIn(BaseModel):
    athlete_id:int
    title:str=Field(min_length=3,max_length=140)
    category:str=Field(default='Performance psychology',max_length=60)
    goal:str=Field(default='',max_length=1200)
    next_review:str|None=None
class NoteIn(BaseModel):
    case_id:int
    note_date:date
    content:str=Field(min_length=5,max_length=16000)
class FactorIn(BaseModel):
    label:str=Field(min_length=2,max_length=120)
    group_name:str=Field(default='Daily monitoring',max_length=120)
    kind:Literal['monitoring','assessment']='monitoring'
    unit:str=Field(default='points',max_length=40)
    low:float=0
    high:float=10
    higher_is:Literal['higher-supportive','lower-supportive','contextual']='contextual'
    @model_validator(mode='after')
    def bounds(self):
        if self.high<=self.low:raise ValueError('Maximum must exceed minimum')
        return self
class ObservationIn(BaseModel):
    athlete_id:int
    case_id:int|None=None
    factor_id:int
    value:float
    observed_on:date
    context:str=Field(default='',max_length=120)
class NoteState(BaseModel):
    expected_revision:int=Field(ge=1)
class AssignIn(BaseModel):
    athlete_id:int
    case_id:int
    intervention_id:int
    dosage:str=Field(min_length=3,max_length=240)
class ReportIn(BaseModel):
    athlete_id:int
    case_id:int
    title:str=Field(min_length=3,max_length=120)
    audience:Literal['practitioner','athlete','coach']='practitioner'
    body:str=Field(min_length=10,max_length=12000)
class ConsentIn(BaseModel):
    confirmed:bool

def create_app(database_url=None,init_database=True):
    mode=os.getenv('MODEL_C_ENV','local').lower()
    # The application has no demo seeding. The retired flag is still refused so an old configuration fails loudly.
    seed_demo=os.getenv('MODEL_C_SEED_DEMO','false').lower()=='true'
    integration_dev=os.getenv('MODEL_C_INTEGRATION_DEV','false').lower()=='true'
    clinical_parity=os.getenv('SPPS_DEV_CLINICAL_PARITY','false').lower()=='true'
    if clinical_parity and not integration_dev:
        raise RuntimeError('Clinical parity requires isolated local PostgreSQL integration development')
    if integration_dev and (mode!='local' or init_database or seed_demo):
        raise RuntimeError('Integration development requires local mode and explicit offline provisioning')
    if mode not in ('local','staging','production'):
        raise RuntimeError('MODEL_C_ENV must be local, staging or production')
    if mode!='local':
        # Fail closed on retired test-profile flags: none of them has any effect in this codebase.
        unsafe={
            'MODEL_C_SEED_DEMO':seed_demo,
            'MODEL_C_PAYMENT_TEST_MODE':os.getenv('MODEL_C_PAYMENT_TEST_MODE','false').lower()=='true',
            'MODEL_C_ALLOW_TEST_ERASURE':os.getenv('MODEL_C_ALLOW_TEST_ERASURE','false').lower()=='true',
            'MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION':os.getenv('MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION','false').lower()=='true',
        }
        if any(unsafe.values()):
            raise RuntimeError('Hosted mode refuses demo seed, simulated payments and test erasure')
        if init_database:
            raise RuntimeError('Hosted mode cannot initialize or migrate the database at app startup')
        if mode=='production' and os.getenv('MODEL_C_PUBLIC_TENANT','').strip().lower() in ('','model-c-demo'):
            raise RuntimeError('Production requires MODEL_C_PUBLIC_TENANT set to the real tenant')
        if os.getenv('MODEL_C_COOKIE_SECURE','false').lower()!='true':
            raise RuntimeError('Hosted mode requires secure session cookies')
        url=database_url or os.getenv('DATABASE_URL','')
        if not url.startswith(('postgresql://','postgresql+psycopg://','postgres://')):
            raise RuntimeError('Hosted mode requires a managed PostgreSQL DATABASE_URL')
        if not os.getenv('MODEL_C_ALLOWED_HOSTS','').strip():
            raise RuntimeError('Hosted mode requires explicit MODEL_C_ALLOWED_HOSTS')
    # Hosted protected RLS profile: least-privilege TLS PostgreSQL in staging and production.
    hosted_rls=os.getenv('SPPS_HOSTED_RLS','false').lower()=='true'
    if hosted_rls:
        from .hosted_profile import validate_startup
        if database_url is not None:
            raise RuntimeError('Hosted RLS profile reads DATABASE_URL from the environment only')
        validate_startup()
    elif mode == 'production' or (mode=='staging' and
            os.getenv('MODEL_C_HOSTED_PILOT_UNLOCKED','false').lower()=='true'):
        raise RuntimeError('Unlocked hosted operation requires protected RLS (SPPS_HOSTED_RLS=true)')
    rls=integration_dev or hosted_rls
    app=FastAPI(title='SPPS Version 1.0 - Free',version='1.0.0',docs_url=None,redoc_url=None)
    if integration_dev:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver'])
    if mode!='local':
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        allowed=[x.strip() for x in os.environ['MODEL_C_ALLOWED_HOSTS'].split(',') if x.strip()]
        if not allowed or any(x=='*' for x in allowed):
            raise RuntimeError('Use explicit hosted hostnames, never a wildcard')
        app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed)
    # Local process-scoped throttle; requires Redis or gateway-enforced rate limiting in hosted environments.
    rate_limits=defaultdict(deque)
    rate_lock=threading.Lock()
    app.state.v2_rls=rls
    app.state.engine=make_engine(database_url)
    app.state.DB=make_session(app.state.engine)
    # v2: record-access audit rows are written by the middleware while the audited request still holds its own
    # connection, so they use a separate one-connection pool; sharing the request pool deadlocks under load.
    if app.state.engine.dialect.name=='sqlite' and app.state.engine.url.database in (None,'',':memory:'):
        app.state.AuditDB=app.state.DB
    else:
        app.state.audit_engine=make_engine(database_url,pool=(1,1))
        app.state.AuditDB=make_session(app.state.audit_engine)
    app.state.AuthDB=app.state.DB
    if integration_dev:
        from .identity import install_identity, check_role, require_local_database
        auth_url=os.getenv('SPPS_AUTH_DATABASE_URL','')
        if not auth_url:
            raise RuntimeError('SPPS_AUTH_DATABASE_URL is required for isolated authentication')
        data_target=require_local_database(database_url or os.getenv('DATABASE_URL',''))
        auth_target=require_local_database(auth_url)
        if (data_target.host,data_target.port,data_target.database)!=(auth_target.host,auth_target.port,auth_target.database):
            raise RuntimeError('Authentication and clinical roles must use the same development database')
        app.state.auth_engine=make_engine(auth_url)
        app.state.AuthDB=make_session(app.state.auth_engine)
        check_role(app.state.engine,'spps_dev_app',require_clinical=clinical_parity)
        check_role(app.state.auth_engine,'spps_dev_auth')
        install_identity(app.state.DB)
        install_identity(app.state.AuthDB)
    if hosted_rls:
        from .identity import install_identity, check_role
        app.state.auth_engine=make_engine(os.environ['SPPS_AUTH_DATABASE_URL'])
        app.state.AuthDB=make_session(app.state.auth_engine)
        # Same refusals as the local milestone: no superuser/BYPASSRLS/ownership/membership, 33 FORCE RLS tables
        # and the clinical write policies present before any clinical write route is reachable.
        check_role(app.state.engine,'spps_dev_app',require_clinical=True)
        check_role(app.state.auth_engine,'spps_dev_auth')
        install_identity(app.state.DB)
        install_identity(app.state.AuthDB)
    if mode=='production':
        from .release_preflight import verify_production
        verify_production(app)
    if init_database:
        from .migrate_local import migrate_local
        migrate_local(app.state.engine)
        Base.metadata.create_all(app.state.engine)
        from .v2.models import V2Base
        V2Base.metadata.create_all(app.state.engine)
        from .v2.athlete_loop import create_local_tables
        create_local_tables(app.state.engine)
    app.mount('/assets',StaticFiles(directory=WEB),name='assets')

    from .v2 import reqlog as _reqlog
    _reqlog.install_logging()

    @app.middleware('http')
    async def request_context(request:Request,call_next):
        # v2: the id is held in a context variable so EVERY log line written during the request carries it.
        rid=_reqlog.incoming_id(request.headers.get('x-request-id',''))
        token=_reqlog.REQUEST_ID.set(rid)
        from .v2 import access as _access
        access_token=_access.begin()
        started=time.monotonic()
        try:
            try:
                from .v2 import guards as _guards
                _guards.global_limit(request)  # v2 phase 2: every public and auth endpoint is rate limited
                response=await call_next(request)
            except HTTPException as exc:
                from fastapi.responses import JSONResponse
                response=JSONResponse({'detail':exc.detail},status_code=exc.status_code)
            except Exception:
                REQUEST_LOG.exception('unhandled error')
                from fastapi.responses import JSONResponse
                response=JSONResponse({'detail':'Internal error','request_id':rid},status_code=500)
            response.headers['X-Request-ID']=rid
            route=request.scope.get('route')
            _access.flush(app,request.method,getattr(route,'path',request.url.path),response.status_code)
            # Path only (no query string, token-like segments masked), status and timing. Never bodies or e-mail.
            REQUEST_LOG.info(_reqlog.access_line(rid,request.method,request.url.path,response.status_code,started))
            return response
        finally:
            _access.end(access_token)
            _reqlog.REQUEST_ID.reset(token)

    @app.middleware('http')
    async def security_headers(request:Request,call_next):
        # Both hosted environments remain closed until their independent release
        # preflights have succeeded. Health checks remain available.
        staging_locked = mode == 'staging' and os.getenv('MODEL_C_HOSTED_PILOT_UNLOCKED','false').lower()!='true'
        public_preview = request.method in ('GET','HEAD') and (request.url.path in ('/','/services','/practitioners','/register','/privacy','/terms','/policies','/guide','/api/public/supabase-auth-config') or request.url.path.startswith('/assets/'))
        if staging_locked and request.method in ('GET','HEAD') and request.url.path=='/favicon.ico':
            response=Response(status_code=204)
        elif staging_locked and request.url.path not in ('/health/live','/health/ready') and not public_preview:
            from fastapi.responses import JSONResponse
            response=JSONResponse({'status':'locked','message':'SPPS staging pilot is not open'},status_code=503)
        elif rls and not development_route_allowed(request.method,request.url.path):
            # Default deny: only routes whose tables carry restricted-role grants and RLS policies are served.
            from fastapi.responses import JSONResponse
            response=JSONResponse({'detail':'This workflow is outside the isolated development milestone'},status_code=503)
        else:
            response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Permissions-Policy']='camera=(self), microphone=(self), geolocation=(), payment=(), usb=()'
        if mode!='local':response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        # Pages whose address can carry a secret token (report links, invitations, verification) and private
        # workspaces are never indexed or stored by browsers and proxies.
        token_page=request.url.path.startswith(('/r/','/s/','/athlete','/portal','/room','/studio','/your-data'))
        response.headers['X-Robots-Tag']='noindex, nofollow' if (staging_locked or token_page) else 'index, follow'
        if staging_locked:
            response.headers['Cache-Control']='no-store'
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://www.winmindperform.com; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        else:
            response.headers['Cache-Control']='no-store' if (token_page or request.url.path.startswith('/api')) else 'no-cache'
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://checkout.razorpay.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://www.winmindperform.com https://checkout.razorpay.com; connect-src 'self' wss: https://api.razorpay.com; font-src 'self'; frame-src https://api.razorpay.com https://checkout.razorpay.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        auth_cfg=sbauth.public_config()
        if auth_cfg["enabled"]:
            # Only the pinned Supabase UMD bundle and this project may be contacted.
            csp=response.headers['Content-Security-Policy']
            csp=csp.replace("script-src 'self'", "script-src 'self' https://cdn.jsdelivr.net")
            csp=csp.replace("connect-src 'self'", "connect-src 'self' " + auth_cfg["url"])
            response.headers['Content-Security-Policy']=csp
        return response

    def db_session():
        db=app.state.DB()
        try:yield db
        finally:db.close()

    def auth_session(shared:Session=Depends(db_session)):
        if not rls:
            yield shared
        else:
            with app.state.AuthDB() as db:
                yield db

    def get_current(request:Request, db:Session=Depends(db_session), auth:Session=Depends(auth_session)):
        raw=request.cookies.get('modelc_session','')
        if not raw:raise HTTPException(401,'Sign in required')
        entry=auth.scalar(select(LoginSession).where(LoginSession.token_hash==hash_token(raw),LoginSession.revoked==False))
        if not entry or is_expired(entry.expires_at):raise HTTPException(401,'Session expired')
        user=auth.get(Practitioner,entry.practitioner_id)
        if not user or not user.active:raise HTTPException(401,'Account disabled')
        if sbauth.enabled() and not entry.csrf_token.startswith('sp1_'):
            raise HTTPException(401,'Sign in with a verified Supabase email session')
        if rls:
            from .identity import bind_identity
            bind_identity(db,user)
            bind_identity(auth,user)
        if (mode!='local' or free_access.enabled()) and not user.is_admin:
            profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
            if not profile or profile.status!='approved':
                raise HTTPException(403,'Your account is pending verification by the platform admin.')
        if not active_membership(auth,user):raise HTTPException(403,'Practitioner membership is not active')
        if request.method not in ('GET','HEAD','OPTIONS'):
            sent=request.headers.get('x-csrf-token') or ''
            if not sent or not secrets.compare_digest(sent,entry.csrf_token):raise HTTPException(403,'CSRF validation failed')
        if rls:
            from .identity import bind_identity
            bind_identity(db,user)
            bind_identity(auth,user)
        return user

    def event(db,user,action,typ,object_id=None):
        db.add(Audit(tenant_id=user.tenant_id,actor_id=user.id,action=action,object_type=typ,object_id=object_id))

    def athlete_for(db,user,athlete_id):
        athlete=db.scalar(select(Athlete).where(Athlete.id==athlete_id,Athlete.tenant_id==user.tenant_id,Athlete.owner_practitioner_id==user.id))
        if not athlete:raise HTTPException(404,'Athlete not found')
        from .v2 import access as _access
        _access.note(user,athlete.id)  # v2 phase 2: record-access audit (who, what, when; never content)
        return athlete
    def case_for(db,user,case_id):
        case=db.scalar(select(Case).where(Case.id==case_id,Case.tenant_id==user.tenant_id,Case.owner_practitioner_id==user.id))
        if not case:raise HTTPException(404,'Case not found')
        athlete_for(db,user,case.athlete_id)
        return case
    def factor_for(db,user,factor_id):
        f=db.scalar(select(Factor).where(Factor.id==factor_id,Factor.tenant_id==user.tenant_id,Factor.active==True,or_(Factor.owner_practitioner_id==user.id,Factor.owner_practitioner_id.is_(None))))
        if not f:raise HTTPException(404,'Factor not found')
        return f
    def tenant_public(db):
        slug=os.getenv('MODEL_C_PUBLIC_TENANT','model-c-demo')
        t=db.scalar(select(Tenant).where(Tenant.slug==slug))
        if not t:raise HTTPException(503,'Services temporarily unavailable')
        return t
    def serialize(obj,*attrs):
        return {a:getattr(obj,a) for a in attrs}

    @app.get('/health/ready')
    def health_ready():
        from sqlalchemy import text, inspect
        try:
            with app.state.engine.connect() as conn:
                conn.execute(text('SELECT 1'))
                tables=set(inspect(conn).get_table_names())
            # v2: every v2 table is required, so a release whose SQL has not been run is never "ready".
            from .v2.models import V2Base
            if not ({'tenants','practitioners','login_sessions','athletes'}|set(V2Base.metadata.tables)).issubset(tables):
                raise ValueError('Staging schema not initialized')
            return {'status':'ready','version':app.version}
        except Exception:
            raise HTTPException(503,'Database not ready')

    @app.get('/health/live')
    def health_live():
        # No database, credentials, practitioner names or internal paths in response.
        return {'status':'ok','version':app.version}

    @app.get('/api/runtime')
    def runtime_profile():
        # integration_development = "restricted RLS profile" for the UI (studio-first, no public directory).
        return {'integration_development':(rls and not (hosted_rls and free_access.enabled())),
                'clinical_parity':clinical_parity or hosted_rls,
                'free_access':free_access.enabled(),
                'portal':os.getenv('SPPS_PORTAL_ENABLED','false').lower()=='true',
                'ai':__import__('server.v2.flags',fromlist=['x']).ai_globally_enabled(),
                'v2_flags':__import__('server.v2.flags',fromlist=['x']).snapshot(),
                'profile':('hosted-rls' if mode=='production' else 'hosted-staging-rls') if hosted_rls else ('local-milestone' if integration_dev else 'standard'),
                'environment':mode,'data_class':'production' if mode=='production' else 'staging',
                'production_ready':False}

    @app.get('/')
    @app.get('/services')
    def public_html():return FileResponse(WEB/'index.html')
    @app.get('/practitioners')
    def directory_html():return FileResponse(WEB/'index.html')
    @app.get('/register')
    def register_html():return FileResponse(WEB/'index.html')
    @app.get('/studio')
    def studio_html():return FileResponse(WEB/'index.html')
    @app.get('/admin')
    def admin_html(user:Practitioner=Depends(get_current)):
        if not user.is_admin:raise HTTPException(403,'Administrator required')
        return FileResponse(WEB/'index.html')
    @app.get('/room/{room_id}')
    def consultation_html(room_id:int):return FileResponse(WEB/'room.html')
    @app.get('/video/{booking_id}')
    def platform_video_html(booking_id:int):return FileResponse(WEB/'room.html')
    @app.get('/portal')
    def portal_html():return FileResponse(WEB/'portal.html')
    from .v2.pages import render_page as _render_page
    @app.get('/privacy')
    def privacy_html():return _render_page('privacy.html')
    @app.get('/terms')
    def terms_html():return _render_page('terms.html')
    @app.get('/policies')
    def policies_html():return _render_page('policies.html')
    @app.get('/guide')
    def guide_html():return _render_page('guide.html')
    @app.get('/api/public/services')
    def services(db:Session=Depends(db_session)):
        t=tenant_public(db)
        if hosted_rls:
            # Anonymous visitors have no table grants; a narrow SECURITY DEFINER function (0029) returns public fields only.
            rows=db.execute(text('SELECT * FROM spps_dev.public_services(:slug)'),{'slug':t.slug}).mappings().all()
            return {'brand':t.display_name,'services':[{k:r[k] for k in ('id','slug','title','description','duration','price_label')} for r in rows]}
        ss=db.scalars(select(Service).where(Service.tenant_id==t.id,Service.active==True).order_by(Service.id)).all()
        return {'brand':t.display_name,'services':[serialize(s,'id','slug','title','description','duration','price_label') for s in ss]}

    @app.get('/api/public/supabase-auth-config')
    def supabase_auth_config():
        return sbauth.public_config()

    @app.post('/api/auth/supabase-exchange')
    def supabase_exchange(payload:SupabaseExchangeIn,response:Response,request:Request,
                          clinical:Session=Depends(db_session),auth:Session=Depends(auth_session)):
        from .v2 import guards as _guards
        _guards.limit_ip(request,'auth-exchange')
        if not payload.terms_accepted:
            raise HTTPException(422,'Accept the terms and privacy notice before signing in')
        # GoTrue verifies this bearer token and confirmed email before any
        # SPPS session is created. AAL1 is sufficient; role approval is not.
        identity=sbauth.verify_access_token(payload.access_token,password_required=True)
        user=auth.scalar(select(Practitioner).where(Practitioner.email==identity['email']))
        is_owner=(identity['email']=='karansportpsy@gmail.com'
                  and os.getenv('SPPS_OWNER_EMAIL','karansportpsy@gmail.com').strip().lower()=='karansportpsy@gmail.com')
        if is_owner and hosted_rls and os.getenv('SPPS_OWNER_BOOTSTRAP_ENABLED','true').lower()=='true':
            # Private SQL binds the verified Supabase subject and refuses a
            # different subject, rejected owner or prior security hold.
            from sqlalchemy.exc import DBAPIError
            try:
                clinical.execute(text("""
                    SELECT spps_dev.claim_verified_platform_owner(
                      CAST(:uid AS uuid),:email,:tenant,:hash
                    )
                """),{'uid':identity['uid'],'email':identity['email'],
                      'tenant':os.getenv('MODEL_C_PUBLIC_TENANT','model-c-demo'),
                      'hash':_make_password(new_token())}).scalar_one()
                clinical.commit()
            except DBAPIError as exc:
                clinical.rollback()
                raise HTTPException(403,'The owner account is on hold or not provisioned. Check the database setup and audit log.') from exc
            auth.expire_all()
            user=auth.scalar(select(Practitioner).where(Practitioner.email==identity['email']))
        if not user and not is_owner:
            # v2 phase 1: promote the server-side pending application from whichever browser signs in first.
            app.state.v2_promote(clinical,tenant_public(clinical),identity)
            auth.expire_all()
            user=auth.scalar(select(Practitioner).where(Practitioner.email==identity['email']))
        if not user:
            raise HTTPException(403,'No practitioner application was found for this email. Register first.')
        if rls:
            from .identity import bind_identity
            bind_identity(clinical,user)
            bind_identity(auth,user)
        profile=clinical.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
        if (not profile or str(profile.supabase_auth_uid or '')!=identity['uid']
            or profile.email_verified_at is None):
            raise HTTPException(403,'Verified Supabase account is not linked to this practitioner')
        if not user.is_admin and (not user.active or profile.status!='approved'):
            # Only a verified, password-authenticated applicant bound to this profile sees the reviewer's note.
            from .practitioner_review import application_message
            raise HTTPException(403,application_message(profile))
        if not user.active:
            raise HTTPException(403,'Your account is pending verification by the platform admin.')
        if not active_membership(auth,user):
            raise HTTPException(403,'Practitioner account not active')
        token=new_token();csrf='sp1_'+new_token()
        auth.add(LoginSession(practitioner_id=user.id,token_hash=hash_token(token),
                              csrf_token=csrf,expires_at=expiry()))
        auth.commit()
        response.set_cookie('modelc_session',token,httponly=True,
                            secure=os.getenv('MODEL_C_COOKIE_SECURE','false').lower()=='true',
                            samesite='strict',max_age=36000,path='/')
        return {'id':user.id,'name':user.name,'csrf':csrf,'tier':user.tier,'is_admin':user.is_admin}

    @app.post('/api/auth/login')
    def login(credentials:LoginIn,response:Response,request:Request,db:Session=Depends(auth_session)):
        from .hosted_profile import client_ip
        client=client_ip(request)
        key='login:'+client
        current=time.monotonic()
        with rate_lock:
            window=rate_limits[key]
            while window and window[0]<current-600:window.popleft()
            if len(window)>=12:raise HTTPException(429,'Sign-in temporarily limited')
            window.append(current)
        # Terms are checked BEFORE credentials so the response never confirms a correct password.
        if not credentials.terms_accepted:raise HTTPException(422,'Please accept the Terms of Use and Privacy Policy to sign in')
        user=db.scalar(select(Practitioner).where(Practitioner.email==credentials.email.lower().strip()))
        # Constant-work check: unknown emails still pay the PBKDF2 cost (no timing oracle).
        password_ok=verify_password(credentials.password,user.password_hash if user else DUMMY_PASSWORD_HASH)
        if not user or not password_ok:
            raise HTTPException(401,'Invalid email or password')
        if (mode!='local' or free_access.enabled()) and not user.is_admin and not user.active:
            raise HTTPException(403,'Your account is pending verification by the platform admin.')
        if not user.active:raise HTTPException(401,'Invalid email or password')
        if sbauth.enabled():
            raise HTTPException(403,'Use the verified Supabase email/password sign-in')
        if (mode!='local' or free_access.enabled()) and not user.is_admin:
            # No profile is read from the unprivileged auth database on hosted installations.
            with app.state.DB() as clinical:
                if rls:
                    from .identity import bind_identity
                    bind_identity(clinical,user)
                profile=clinical.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
            if not profile or profile.status!='approved':
                raise HTTPException(403,'Your account is pending verification by the platform admin.')
        if not active_membership(db,user):raise HTTPException(403,'Practitioner membership inactive')
        if rls:
            from .identity import bind_identity
            bind_identity(db,user)
        db.add(TermsAcceptance(practitioner_id=user.id,version='v1.0-free'))
        token=new_token(); csrf=new_token()
        entry=LoginSession(practitioner_id=user.id,token_hash=hash_token(token),csrf_token=csrf,expires_at=expiry())
        db.add(entry);event(db,user,'login','session');db.commit()
        use_secure=os.getenv('MODEL_C_COOKIE_SECURE','false').lower()=='true'
        response.set_cookie('modelc_session',token,httponly=True,secure=use_secure,samesite='strict',max_age=36000,path='/')
        return {'id':user.id,'name':user.name,'csrf':csrf,'tier':user.tier,'is_admin':user.is_admin}

    @app.get('/api/auth/me')
    def me(request:Request,user:Practitioner=Depends(get_current),db:Session=Depends(auth_session)):
        raw=request.cookies.get('modelc_session','')
        entry=db.scalar(select(LoginSession).where(LoginSession.token_hash==hash_token(raw)))
        return {'id':user.id,'name':user.name,'email':user.email,'csrf':entry.csrf_token,'tier':user.tier,'is_admin':user.is_admin}
    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response,user:Practitioner=Depends(get_current),db:Session=Depends(auth_session)):
        entry=db.scalar(select(LoginSession).where(LoginSession.token_hash==hash_token(request.cookies.get('modelc_session',''))))
        entry.revoked=True;event(db,user,'logout','session',entry.id);db.commit()
        response.delete_cookie('modelc_session',path='/');return {'ok':True}

    @app.get('/api/dashboard')
    def dashboard(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        tid=user.tenant_id
        count=lambda model,clause:db.scalar(select(func.count()).select_from(model).where(*clause))
        return {'stats':{'athletes':count(Athlete,[Athlete.tenant_id==tid,Athlete.owner_practitioner_id==user.id]),'active_cases':count(Case,[Case.tenant_id==tid,Case.owner_practitioner_id==user.id,Case.status=='active']),'open_drafts':count(Note,[Note.tenant_id==tid,Note.author_id==user.id,Note.state=='draft'])}}

    @app.get('/api/athletes')
    def list_athletes(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        rows=db.scalars(select(Athlete).where(Athlete.tenant_id==user.tenant_id,Athlete.owner_practitioner_id==user.id).order_by(Athlete.display_name)).all()
        from .v2 import access as _access
        _access.note(user,None)  # the caseload list itself is an access to athlete records
        return [serialize(a,'id','code','display_name','sport','athlete_group','active') for a in rows]
    @app.post('/api/athletes',status_code=201)
    def add_athlete(payload:AthleteIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if db.scalar(select(Athlete.id).where(Athlete.tenant_id==user.tenant_id,Athlete.owner_practitioner_id==user.id,Athlete.code==payload.code)):raise HTTPException(409,'Athlete code exists')
        a=Athlete(tenant_id=user.tenant_id,owner_practitioner_id=user.id,code=payload.code,display_name=payload.display_name,sport=payload.sport,athlete_group=payload.athlete_group,service_consent_recorded_at=now() if payload.service_consent_recorded else None)
        db.add(a);db.flush();event(db,user,'athlete.created','athlete',a.id);db.commit();return {'id':a.id}
    @app.get('/api/athletes/{athlete_id}')
    def athlete_detail(athlete_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,athlete_id)
        cases=db.scalars(select(Case).where(Case.athlete_id==a.id,Case.tenant_id==user.tenant_id).order_by(Case.created_at.desc())).all()
        return {'athlete':{**serialize(a,'id','code','display_name','sport','athlete_group','active','created_at'),'service_consent_recorded':bool(a.service_consent_recorded_at)}, 'cases':[serialize(c,'id','title','category','goal','status','next_review','created_at') for c in cases]}
    @app.post('/api/athletes/{athlete_id}/service-consent')
    def mark_consent(athlete_id:int,payload:ConsentIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,athlete_id)
        if not payload.confirmed:raise HTTPException(422,'Affirmative consent confirmation required')
        if not a.service_consent_recorded_at:
            a.service_consent_recorded_at=now()
            event(db,user,'consent.recorded','athlete',a.id)
            db.commit()
        return {'ok':True}
    @app.post('/api/cases',status_code=201)
    def new_case(payload:CaseIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,payload.athlete_id)
        if not a.service_consent_recorded_at:raise HTTPException(403,'Document service intake consent before opening a clinical case')
        c=Case(tenant_id=user.tenant_id,athlete_id=a.id,owner_practitioner_id=user.id,title=payload.title,category=payload.category,goal=payload.goal,next_review=payload.next_review)
        db.add(c);db.flush();event(db,user,'case.created','case',c.id);db.commit();return {'id':c.id}
    @app.get('/api/cases/{case_id}')
    def case_detail(case_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        c=case_for(db,user,case_id)
        n=db.scalars(select(Note).where(Note.case_id==c.id,Note.tenant_id==user.tenant_id).order_by(Note.note_date.desc(),Note.id.desc())).all()
        assignments=db.scalars(select(Assignment).where(Assignment.case_id==c.id,Assignment.tenant_id==user.tenant_id)).all()
        return {'case':serialize(c,'id','title','category','goal','status','next_review','athlete_id'),'notes':[serialize(z,'id','note_date','content','state','revision','created_at') for z in n],'assignments':[{**serialize(a,'id','dosage','status'), 'intervention':db.get(Intervention,a.intervention_id).name} for a in assignments]}
    @app.post('/api/notes',status_code=201)
    def add_note(payload:NoteIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        c=case_for(db,user,payload.case_id)
        n=Note(tenant_id=user.tenant_id,case_id=c.id,author_id=user.id,note_date=payload.note_date.isoformat(),content=payload.content)
        db.add(n);db.flush();event(db,user,'note.created','note',n.id);db.commit();return {'id':n.id,'state':n.state}
    @app.post('/api/notes/{note_id}/finalize')
    def finalize_note(note_id:int,payload:NoteState,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        n=db.scalar(select(Note).where(Note.id==note_id,Note.tenant_id==user.tenant_id,Note.author_id==user.id))
        if not n:raise HTTPException(404,'Note not found')
        case_for(db,user,n.case_id)
        if n.state!='draft' or n.revision!=payload.expected_revision:raise HTTPException(409,'Note state or revision mismatch')
        n.state='final';n.revision+=1;event(db,user,'note.finalized','note',n.id);db.commit();return {'state':n.state,'revision':n.revision}
    @app.get('/api/factors')
    def factors(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        return [serialize(f,'id','label','group_name','kind','unit','low','high','higher_is','version') for f in db.scalars(select(Factor).where(Factor.tenant_id==user.tenant_id,Factor.active==True,or_(Factor.owner_practitioner_id==user.id,Factor.owner_practitioner_id.is_(None))).order_by(Factor.kind,Factor.label))]
    @app.post('/api/factors',status_code=201)
    def add_factor(payload:FactorIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        f=Factor(tenant_id=user.tenant_id,owner_practitioner_id=user.id,**payload.model_dump());db.add(f);db.flush();event(db,user,'factor.created','factor',f.id);db.commit();return {'id':f.id}
    @app.post('/api/observations',status_code=201)
    def record_observation(payload:ObservationIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,payload.athlete_id)
        if payload.case_id:
            c=case_for(db,user,payload.case_id)
            if c.athlete_id!=a.id:raise HTTPException(400,'Case does not belong to athlete')
        f=factor_for(db,user,payload.factor_id)
        if not f.low<=payload.value<=f.high:raise HTTPException(422,f'Score must be between {f.low} and {f.high}')
        o=Observation(tenant_id=user.tenant_id,athlete_id=a.id,case_id=payload.case_id,factor_id=f.id,value=payload.value,observed_on=payload.observed_on.isoformat(),context=payload.context)
        db.add(o);db.flush();event(db,user,'observation.created','observation',o.id);db.commit();return {'id':o.id}
    @app.get('/api/athletes/{athlete_id}/observations')
    def get_observations(athlete_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        athlete_for(db,user,athlete_id)
        entries=db.scalars(select(Observation).where(Observation.athlete_id==athlete_id,Observation.tenant_id==user.tenant_id).order_by(Observation.observed_on,Observation.id)).all()
        out=[]
        for o in entries:
            f=db.get(Factor,o.factor_id)
            out.append({**serialize(o,'id','value','observed_on','source','context','factor_id','case_id'), 'label':f.label,'kind':f.kind,'unit':f.unit,'low':f.low,'high':f.high,'version':f.version,'group_name':f.group_name})
        return out
    @app.get('/api/interventions')
    def interventions(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        return [serialize(i,'id','name','purpose','method','evidence_note','reference_url','approved') for i in db.scalars(select(Intervention).where(Intervention.tenant_id==user.tenant_id).order_by(Intervention.name))]
    @app.post('/api/assignments',status_code=201)
    def assign(payload:AssignIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,payload.athlete_id)
        c=case_for(db,user,payload.case_id)
        if c.athlete_id!=a.id:raise HTTPException(400,'Mismatched case')
        i=db.scalar(select(Intervention).where(Intervention.id==payload.intervention_id,Intervention.tenant_id==user.tenant_id))
        if not i:raise HTTPException(404,'Intervention not found')
        entry=Assignment(tenant_id=user.tenant_id,athlete_id=a.id,case_id=c.id,intervention_id=i.id,dosage=payload.dosage)
        db.add(entry);db.flush();event(db,user,'intervention.assigned','assignment',entry.id);db.commit();return {'id':entry.id}
    @app.post('/api/reports',status_code=201)
    def new_report(payload:ReportIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        c=case_for(db,user,payload.case_id)
        if c.athlete_id!=payload.athlete_id:raise HTTPException(400,'Case mismatch')
        # No automatic report release or coach portal in Model C; drafts are private.
        r=Report(tenant_id=user.tenant_id,athlete_id=c.athlete_id,case_id=c.id,title=payload.title,audience=payload.audience,body=payload.body,status='draft')
        db.add(r);db.flush();event(db,user,'report.drafted','report',r.id);db.commit();return {'id':r.id,'status':r.status}
    @app.get('/api/reports/{report_id}/export')
    def export_report(report_id:int,format:Literal['csv','pdf']='pdf',user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if not free_access.enabled() and user.tier!='pro':raise HTTPException(403,'Pro plan required for report exports')
        r=db.scalar(select(Report).where(Report.id==report_id,Report.tenant_id==user.tenant_id))
        if not r:raise HTTPException(404,'Report not found')
        c=case_for(db,user,r.case_id)
        if c.athlete_id!=r.athlete_id:raise HTTPException(403,'Case mismatch')
        event(db,user,'report.exported','report',r.id);db.commit()
        if format=='csv':
            import csv,io
            output=io.StringIO();writer=csv.writer(output)
            writer.writerow(['Status','Title','Audience','Case ID','Body'])
            safe=lambda v:("'"+str(v)) if str(v).startswith(('=','+','-','@')) else v
            writer.writerow([r.status,safe(r.title),r.audience,c.id,safe(r.body)])
            raw=output.getvalue().encode('utf-8-sig')
            return StreamingResponse(iter([raw]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="spps-report-{r.id}.csv"','Cache-Control':'no-store'})
        import io,html
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
        from reportlab.lib import colors
        memory=io.BytesIO();doc=SimpleDocTemplate(memory,pagesize=A4,rightMargin=48,leftMargin=48,topMargin=48,bottomMargin=48)
        styles=getSampleStyleSheet();elements=[]
        for label,text in [('SPPS · PRACTITIONER DRAFT — NOT APPROVED FOR DISCLOSURE', ''),('Report title',r.title),('Intended audience',r.audience),('Draft status',r.status)]:
            if label:elements.append(Paragraph(html.escape(label),styles['Heading3']))
            if text:elements.append(Paragraph(html.escape(str(text)),styles['Normal']))
        elements.append(Spacer(1,14))
        for line in r.body.splitlines():elements.append(Paragraph(html.escape(line or ' '),styles['BodyText']))
        elements.append(Spacer(1,18));elements.append(Paragraph('SPPS Version 1.0 - Free',styles['Italic']))
        doc.build(elements);memory.seek(0)
        return StreamingResponse(memory,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="spps-report-{r.id}.pdf"','Cache-Control':'no-store'})

    @app.get('/api/athletes/{athlete_id}/reports')
    def reports(athlete_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        athlete_for(db,user,athlete_id)
        return [serialize(r,'id','title','audience','body','status','created_at') for r in db.scalars(select(Report).where(Report.athlete_id==athlete_id,Report.tenant_id==user.tenant_id).order_by(Report.created_at.desc()))]
    from .beta_routes import register_beta_routes
    register_beta_routes(app,db_session,get_current,athlete_for,case_for,event,tenant_public,serialize)
    register_subscription_routes(app,db_session,tenant_public,event)
    from .marketplace_routes import register_marketplace_routes
    register_marketplace_routes(app,db_session,get_current,tenant_public,event)
    from .practitioner_review import register_review_routes
    register_review_routes(app,db_session,get_current,event,auth_session)
    from .v2 import install as _install_v2
    from .v2.context import Ctx as _Ctx
    _install_v2(app,_Ctx(db_session=db_session,auth_session=auth_session,get_current=get_current,event=event,
                         tenant_public=tenant_public,athlete_for=athlete_for,case_for=case_for,rls=rls,
                         hosted_rls=hosted_rls,mode=mode,extra={'serialize':serialize}))
    from . import account_lifecycle
    if account_lifecycle.enabled():
        account_lifecycle.register_lifecycle_routes(app,db_session,get_current,event)
    if os.getenv('SPPS_PORTAL_ENABLED','false').lower()=='true':
        # Stage 13A.5: athlete/guardian portal auth on the separate identity database.
        if not rls:
            raise RuntimeError('Portal authentication requires the isolated local milestone or the hosted RLS profile')
        from .portal_auth import register_portal_routes
        register_portal_routes(app,mode=mode)
        from .portal_links import register_link_routes
        register_link_routes(app,db_session,get_current,athlete_for,event)
        from .bookings import register_booking_routes
        register_booking_routes(app,mode=mode,db_session=db_session,get_current=get_current,event=event)
        from .programmes import register_programme_routes
        register_programme_routes(app,db_session,get_current,athlete_for,event)
        from .video import register_video_routes
        register_video_routes(app,mode=mode,get_current=get_current)
        from .licensing import register_licensing_routes
        register_licensing_routes(app,get_current=get_current,db_session=db_session,event=event)
    return app

app=create_app(init_database=os.getenv('MODEL_C_INIT_DB','false').lower()=='true')
