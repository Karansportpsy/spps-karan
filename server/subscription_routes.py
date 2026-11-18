"""Practitioner sign-up and recurring paid memberships, independent of consultation payments.

Never provision access from browser-side payment claims. Razorpay subscription webhooks
must pass HMAC verification AND the provider subscription read must confirm active state.
All simulation routes are loopback-only AND require explicit environment opt-in.
"""
import os, io, re, json, base64, hmac, hashlib
from datetime import datetime, timezone, timedelta
from urllib.request import Request as UrlRequest, urlopen
from fastapi import HTTPException, Depends, Request, Response
from pydantic import BaseModel, Field, EmailStr, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from PIL import Image, UnidentifiedImageError
from .models import (Tenant, Practitioner, PractitionerProfile, PractitionerSubscription,
                     RegistrationIntent, SubscriptionCharge, TermsAcceptance, Audit, now)
from .security import make_password, new_token, hash_token, is_expired
from . import free_access
from . import supabase_practitioner_auth as sbauth

PLANS={'core':19900,'pro':49900}  # paise per monthly billing period, not INR

# Extra entitlement sources (e.g. institutional seats in the platform DB), registered at startup.
MEMBERSHIP_SOURCES=[]

def active_membership(db:Session, user:Practitioner):
    if free_access.enabled():
        return free_access.active_practitioner(user)
    sub=db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id==user.id))
    paid=False
    if sub and sub.status=='active' and sub.plan==user.tier and sub.current_period_end:
        end=sub.current_period_end
        if end.tzinfo is None:end=end.replace(tzinfo=timezone.utc)
        paid=end>now() and sub.amount_paise==PLANS.get(sub.plan)
    return paid or any(source(user) for source in MEMBERSHIP_SOURCES)

def listing_eligible(db:Session, user:Practitioner, profile:PractitionerProfile|None, lifecycle_db:Session|None=None)->bool:
    """Stage 13D: three INDEPENDENT gates must all hold for public listing/booking:
    independent credential verification, provider-confirmed active membership and an
    administrator's publication approval. Payment alone never publishes a profile."""
    if not bool(user and user.active and profile and profile.status == 'approved'
                and profile.credentials_verified and profile.public_approved
                and active_membership(db,user)):
        return False
    from . import account_lifecycle  # a practitioner may take their own listing offline (migration 0032)
    # lifecycle_db: the application-role session, for callers whose `db` is the restricted auth-role session
    # (which has no right to read practitioner_profiles).
    return not (account_lifecycle.enabled() and account_lifecycle.is_deactivated(lifecycle_db or db,user.id))

def read_avatar(encoded):
    if not encoded: return None
    if len(encoded)>950000:raise HTTPException(413,'Profile image is too large')
    try:
        src=encoded.split(',',1)[-1]  # accept data:image/png;base64 or plain base64
        data=base64.b64decode(src,validate=True)
        if len(data)>650000:raise ValueError('Photo exceeds 650 KB limit')
        image=Image.open(io.BytesIO(data));image.verify()
        image=Image.open(io.BytesIO(data))
        if image.format not in ('JPEG','PNG','WEBP'):raise ValueError('Unsupported photo format')
        if image.width*image.height>12_000_000:raise ValueError('Photo dimensions exceed limit')
        image=image.convert('RGB');image.thumbnail((512,512))
        out=io.BytesIO();image.save(out,format='WEBP',quality=80,method=4)
        if out.tell()>250000:raise ValueError('Processed image exceeds size limit')
        return out.getvalue()
    except (ValueError,UnidentifiedImageError,OSError) as exc:
        raise HTTPException(422,'Please supply a valid JPEG, PNG or WebP profile photo') from exc

def provider_request(method,path,payload=None):
    key=os.getenv('RAZORPAY_KEY_ID','');secret=os.getenv('RAZORPAY_KEY_SECRET','')
    if not key or not secret:raise HTTPException(503,'Recurring payment gateway is not configured')
    basic=base64.b64encode((key+':'+secret).encode()).decode()
    req=UrlRequest('https://api.razorpay.com/v1'+path,
       data=json.dumps(payload).encode() if payload is not None else None,
       headers={'Authorization':'Basic '+basic,'Content-Type':'application/json'},method=method)
    try:
        with urlopen(req,timeout=12) as r:return json.loads(r.read(100000))
    except Exception as exc:raise HTTPException(502,'Subscription payment provider unavailable') from exc

class RegistrationIn(BaseModel):
    name:str=Field(min_length=3,max_length=120)
    email:EmailStr
    password:str|None=Field(default=None,min_length=10,max_length=200)
    supabase_access_token:str|None=Field(default=None,max_length=8192)
    qualifications:str=Field(min_length=4,max_length=1000)
    experience_years:int=Field(ge=0,le=70)
    achievements:str=Field(default='',max_length=1500)
    specialisms:str=Field(default='',max_length=250)
    consultation_fee_paise:int=Field(ge=0,le=10000000)
    plan:str
    photo_base64:str=Field(min_length=24,max_length=950000)
    terms_accepted:bool
    @model_validator(mode='after')
    def check(self):
        if free_access.enabled():
            if sbauth.enabled() and not self.supabase_access_token:
                raise ValueError('Email verification through Supabase Auth is required')
            if not sbauth.enabled() and not self.password:
                raise ValueError('A practitioner password is required')
            if self.plan!='free' or self.consultation_fee_paise!=0:
                raise ValueError('SPPS Version 1.0 - Free registration is free, with no consultation charge')
        elif self.plan not in PLANS or self.consultation_fee_paise<10000:
            raise ValueError('Select Core or Pro and a valid fee')
        if not self.terms_accepted:raise ValueError('You must agree to the Terms of Use and Privacy Policy')
        return self

class RegistrationTokenIn(BaseModel):
    registration_token:str=Field(min_length=35,max_length=150)

def register_subscription_routes(app,db_session,tenant_public,event):
    def lookup_intent(db, token):
        intent=db.scalar(select(RegistrationIntent).where(RegistrationIntent.token_hash==hash_token(token)))
        if not intent or is_expired(intent.expires_at):raise HTTPException(403,'Registration session expired')
        user=db.get(Practitioner,intent.practitioner_id)
        sub=db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id==user.id))
        if not sub:raise HTTPException(404,'Subscription unavailable')
        return user,sub

    @app.get('/api/public/practitioner-plans')
    def plans():
        if free_access.enabled():
            return {'currency':'INR','billing_period':None,'plans':[{'id':'free','name':'Free Open Access','amount_paise':0,'software':True,'directory':True,'booking':True,'video':True,'export':True,'ai_available':False}], 'notice':'No payment, subscription or consultation charge in SPPS Version 1.0 - Free.', 'test_activation_available':False}
        return {'currency':'INR','billing_period':'monthly','plans':[
           {'id':'core','name':'Base / Core','amount_paise':19900,'software':True,'directory':True,'booking':True,'video':True,'export':False,'ai_available':False},
           {'id':'pro','name':'Pro / AI','amount_paise':49900,'software':True,'directory':True,'booking':True,'video':True,'export':True,'ai_available':False}],
           'notice':'AI tools are not part of SPPS Version 1.0 - Free. Pro grants report exports; AI is a planned feature, not currently available.', 'test_activation_available':False}

    @app.post('/api/public/practitioner-registration',status_code=201)
    def register(payload:RegistrationIn,db:Session=Depends(db_session)):
        email=str(payload.email).lower().strip()
        hosted_rls=(os.getenv('MODEL_C_ENV','local').lower() in ('staging','production')
                    and os.getenv('SPPS_HOSTED_RLS','false').lower()=='true')
        if hosted_rls:
            if email=='karansportpsy@gmail.com':
                raise HTTPException(409,'The verified owner must sign in rather than submit a practitioner registration')
            # No anonymous table INSERT grants. Supabase confirms identity and email;
            # the private SECURITY DEFINER function hardcodes inactive/free/non-admin
            # and pending-only values, atomically creating identity, profile and audit.
            if not free_access.enabled() or not sbauth.enabled():
                raise HTTPException(503,'Verified free practitioner registration is not yet available')
            verified=sbauth.verify_access_token(payload.supabase_access_token)
            if verified['email']!=email:
                raise HTTPException(403,'Registration email must match the verified Supabase account')
            avatar=read_avatar(payload.photo_base64)
            password_hash=make_password(new_token())  # unusable local sign-in
            try:
                db.execute(text("""
                    SELECT spps_dev.register_verified_free_practitioner(
                        CAST(:auth_uid AS uuid),:email,:tenant_slug,:name,:password_hash,
                        :qualifications,:experience_years,:achievements,:specialisms,:avatar
                    )
                    """),{
                    'auth_uid':verified['uid'],'email':email,
                    'tenant_slug':os.getenv('MODEL_C_PUBLIC_TENANT','model-c-demo'),
                    'name':payload.name.strip(),'password_hash':password_hash,
                    'qualifications':payload.qualifications,
                    'experience_years':payload.experience_years,
                    'achievements':payload.achievements,
                    'specialisms':payload.specialisms,'avatar':avatar,
                }).scalar_one()
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409,'The verified account is already registered') from exc
            return {
                'status':'pending_approval','plan':'free','amount_paise':0,
                'directory_visible':False,
                'next_step':'Your account is pending verification by the platform admin. '
                            'You cannot access the workspace or directory until approved.'
            }
        t=tenant_public(db)
        if db.scalar(select(Practitioner.id).where(Practitioner.email==email)):
            raise HTTPException(409,'Email already registered; sign in or contact support')
        avatar=read_avatar(payload.photo_base64)
        verified=None
        if free_access.enabled() and sbauth.enabled():
            verified=sbauth.verify_access_token(payload.supabase_access_token)
            if verified['email']!=email:
                raise HTTPException(403,'Registration email must match the verified Supabase account')
            if db.scalar(select(PractitionerProfile.id).where(PractitionerProfile.supabase_auth_uid==verified['uid'])):
                raise HTTPException(409,'Supabase identity already registered')
        if free_access.enabled():
            user=Practitioner(tenant_id=t.id,email=email,name=payload.name.strip(),
                              password_hash=make_password(new_token() if verified else payload.password),tier='free',active=False,is_admin=False)
            db.add(user);db.flush()
            db.add(PractitionerProfile(practitioner_id=user.id,qualifications=payload.qualifications,
                experience_years=payload.experience_years,achievements=payload.achievements,
                specialisms=payload.specialisms,avatar_bytes=avatar,consultation_fee_paise=0,
                public_approved=False,credentials_verified=False,status='pending_approval',
                supabase_auth_uid=verified['uid'] if verified else None,
                email_verified_at=datetime.fromisoformat(verified['email_confirmed_at'].replace('Z','+00:00')) if verified else None))
            db.add(TermsAcceptance(practitioner_id=user.id,version='v1.0-free'))
            event(db,user,'registration.free_pending_credential_review','practitioner',user.id)
            db.commit()
            return {'status':'pending_approval','plan':'free','amount_paise':0,'directory_visible':False,
                    'next_step':'Your account is pending verification by the platform admin. You cannot access the workspace or directory until approved.'}
        user=Practitioner(tenant_id=t.id,email=email,name=payload.name.strip(),
                          password_hash=make_password(payload.password),tier=payload.plan,active=True,is_admin=False)
        db.add(user);db.flush()
        db.add(PractitionerProfile(practitioner_id=user.id,qualifications=payload.qualifications,
           experience_years=payload.experience_years,achievements=payload.achievements,
           specialisms=payload.specialisms,avatar_bytes=avatar,consultation_fee_paise=payload.consultation_fee_paise,
           public_approved=False,credentials_verified=False))
        sub=PractitionerSubscription(practitioner_id=user.id,plan=payload.plan,
                 amount_paise=PLANS[payload.plan],status='pending')
        db.add(sub)
        tok=new_token()
        db.add(RegistrationIntent(practitioner_id=user.id,token_hash=hash_token(tok),
                                  expires_at=now()+timedelta(hours=48)))
        db.add(TermsAcceptance(practitioner_id=user.id,version='v1.0-free-registration'))
        event(db,user,'registration.pending_payment','practitioner',user.id)
        db.commit()
        return {'registration_token':tok,'plan':payload.plan,'amount_paise':PLANS[payload.plan],
                'status':'pending_payment','directory_visible':False,
                'next_step':'Complete recurring subscription checkout. Access requires provider-confirmed activation.'}

    # Hosted free release does not expose recurring-payment, checkout,
    # webhook or test-activation routes. Preserve historical handlers in
    # source for migration audit, but do not register them with FastAPI.
    if free_access.enabled() and os.getenv('MODEL_C_ENV','local').lower() in ('staging','production'):
        return

    @app.post('/api/public/practitioner-registration/checkout')
    def start_checkout(payload:RegistrationTokenIn,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Subscriptions are unavailable in SPPS Version 1.0 - Free')
        user,sub=lookup_intent(db,payload.registration_token)
        if active_membership(db,user):return {'status':'already_active'}
        plan_env='RAZORPAY_PLAN_CORE_ID' if sub.plan=='core' else 'RAZORPAY_PLAN_PRO_ID'
        provider_plan=os.getenv(plan_env,'')
        if not provider_plan:raise HTTPException(503,'Monthly Razorpay subscription plan is not configured')
        if sub.provider_subscription_id:
            existing=provider_request('GET','/subscriptions/'+sub.provider_subscription_id)
            return {'status':existing.get('status','pending'),'checkout_url':existing.get('short_url'),
                    'subscription_id':sub.provider_subscription_id}
        created=provider_request('POST','/subscriptions',{'plan_id':provider_plan,
            'total_count':120,'quantity':1,'customer_notify':0,'notes':{'practitioner_id':str(user.id)}})
        if not created.get('id') or created.get('plan_id')!=provider_plan:
            raise HTTPException(502,'Subscription provider returned an invalid result')
        sub.provider_subscription_id=created['id'];sub.provider_plan_id=provider_plan
        db.commit()
        return {'status':created.get('status','created'),'checkout_url':created.get('short_url'),
                'subscription_id':sub.provider_subscription_id,'key_id':os.getenv('RAZORPAY_KEY_ID',''),
                'notice':'Complete the recurring mandate using the provider link. Access activates only after provider-confirmed subscription activation.'}

    @app.post('/api/public/practitioner-registration/status')
    def registration_status(payload:RegistrationTokenIn,db:Session=Depends(db_session)):
        user,sub=lookup_intent(db,payload.registration_token)
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
        return {'status':'active' if active_membership(db,user) else sub.status,'plan':sub.plan,
                'directory_visible':listing_eligible(db,user,profile),
                'credentials_verified':bool(profile and profile.credentials_verified),
                'publication_approved':bool(profile and profile.public_approved)}

    def apply_subscription(db,sub,remote,*,source):
        if remote.get('id')!=sub.provider_subscription_id or remote.get('plan_id')!=sub.provider_plan_id:
            raise HTTPException(409,'Subscription account or plan mismatch')
        user=db.get(Practitioner,sub.practitioner_id)
        status=remote.get('status')
        if status!='active':
            sub.status='suspended' if status in ('halted','paused','pending','cancelled','expired') else 'pending'
            db.commit()
            return sub
        start,end=remote.get('current_start'),remote.get('current_end')
        if not isinstance(start,int) or not isinstance(end,int) or end<=int(now().timestamp()):
            raise HTTPException(409,'Provider did not confirm a current paid period')
        sub.current_period_start=datetime.fromtimestamp(start,timezone.utc)
        sub.current_period_end=datetime.fromtimestamp(end,timezone.utc)
        sub.status='active';sub.test_mode=False
        user.tier=sub.plan
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
        # Stage 13D: membership activation is NOT publication. Credential verification and
        # administrator publication approval are separate gates (see listing_eligible).
        db.add(Audit(tenant_id=user.tenant_id,actor_id=None,action='subscription.verified_'+source,
                     object_type='subscription',object_id=sub.id))
        db.commit();return sub

    @app.post('/api/payment/razorpay/subscription-webhook')
    async def subscription_webhook(request:Request,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Payment webhooks are disabled in SPPS Version 1.0 - Free')
        secret=os.getenv('RAZORPAY_WEBHOOK_SECRET','')
        if not secret:raise HTTPException(503,'Payment webhook is not configured')
        raw=await request.body()
        if len(raw)>60000:raise HTTPException(413,'Webhook too large')
        signature=request.headers.get('x-razorpay-signature','')
        expected=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature):raise HTTPException(403,'Invalid webhook signature')
        try:payload=json.loads(raw)
        except Exception:raise HTTPException(400,'Malformed webhook')
        kind=payload.get('event','')
        permitted={'subscription.activated','subscription.charged','subscription.pending','subscription.halted',
                   'subscription.paused','subscription.cancelled','subscription.completed','subscription.resumed'}
        if kind not in permitted:return {'ignored':True}
        sub_id=payload.get('payload',{}).get('subscription',{}).get('entity',{}).get('id')
        sub=db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.provider_subscription_id==sub_id))
        if not sub:raise HTTPException(404,'Subscription not found')
        remote=provider_request('GET','/subscriptions/'+sub_id)
        # Status 'active' alone is not payment proof: the first charge must be captured.
        if kind!='subscription.charged':
            if remote.get('status') in ('halted','paused','pending','cancelled','expired'):
                sub.status='suspended';db.commit()
            return {'accepted':True,'status':sub.status,'awaiting_verified_charge':sub.status!='active'}
        pay=payload.get('payload',{}).get('payment',{}).get('entity',{})
        pid=pay.get('id','')
        if not pid:raise HTTPException(422,'Subscription charge does not identify a payment')
        confirmed=provider_request('GET','/payments/'+pid)
        if confirmed.get('status')!='captured' or confirmed.get('amount')!=sub.amount_paise or confirmed.get('currency')!='INR':
            raise HTTPException(409,'Subscription charge has not been independently verified')
        if confirmed.get('subscription_id') and confirmed['subscription_id']!=sub_id:
            raise HTTPException(409,'Subscription payment account mismatch')
        # The provider subscription must also be active for the charged period.
        if remote.get('status')!='active':raise HTTPException(409,'Subscription is not currently active')
        existing=db.scalar(select(SubscriptionCharge).where(SubscriptionCharge.provider_payment_id==pid))
        if existing and existing.subscription_id!=sub.id:raise HTTPException(409,'Payment already belongs to another subscription')
        if not existing:
            db.add(SubscriptionCharge(subscription_id=sub.id,provider_payment_id=pid,amount_paise=sub.amount_paise))
        sub.last_payment_id=pid
        apply_subscription(db,sub,remote,source='captured_charge')
        return {'accepted':True,'status':sub.status,'charge_recorded':True}

    # Internal handles (registration lookup, audit event) for provider callbacks and the automated tests.
    app.state.subscription_internals={'lookup_intent':lookup_intent,'event':event,'db_session':db_session}
