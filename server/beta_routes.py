"""SPPS Version 1.0 - Free standalone features. Public commerce is intentionally separated from confidential athlete records.
No facial-expression classification, biometric recording or unverified AI diagnosis is performed.
"""
from __future__ import annotations
import os, json, hmac, hashlib, secrets, asyncio, base64
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError
from collections import defaultdict
from fastapi import Depends, Request, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, EmailStr, model_validator
from sqlalchemy import select, delete, text
from sqlalchemy.orm import Session
from .models import (Practitioner, PractitionerProfile, AppointmentSlot, Booking, PaymentAllocation, ConsultationRoom,
 RoomAccess, SessionReview, Service, Inquiry, Athlete, Case, Note, Factor, Observation, Assignment, Report,
 AssessmentAdministration, AssessmentSubscore, InjuryEpisode, InjuryCheckIn, RetentionHold, Audit, now)
from .security import new_token, hash_token, is_expired
from .subscription_routes import active_membership,listing_eligible
from . import free_access

def redact_public_profile(value:str)->str:
    """Defense-in-depth against contact details embedded in credentials/achievements."""
    import re
    value=value or ''
    value=re.sub(r'(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b','[contact omitted]',value)
    value=re.sub(r'(?i)(?:https?://|www\.)\S+','[link omitted]',value)
    value=re.sub(r'(?i)\b(?:whatsapp|telegram|instagram|linkedin|phone|email)\s*[:=]\s*\S+','[contact omitted]',value)
    value=re.sub(r'(?<!\w)(?:\+?\d[\d ()-]{8,}\d)(?!\w)','[contact omitted]',value)
    return value.strip()

VALID_TIERS={'core':0,'pro':1}
METRIC_UNITS={'Raw Score','Percentile','Z-Score','T-Score','Sten'}
STAGES={'early rehabilitation','mid rehabilitation','late rehabilitation','return-to-training','return-to-competition','closed'}

class SlotIn(BaseModel):
    starts_at:datetime
    duration_minutes:int=Field(default=50,ge=20,le=120)
    amount_paise:int=Field(ge=0,le=10000000)

class BookIn(BaseModel):
    slot_id:int
    contact_name:str=Field(min_length=2,max_length=120)
    contact_email:EmailStr
    is_minor:bool=False
    guardian_confirmed:bool=False
    contact_consent:bool
    video_consent:bool=False
    @model_validator(mode='after')
    def valid_consent(self):
        if not self.contact_consent:raise ValueError('Contact and booking consent required')
        if self.is_minor and not self.guardian_confirmed:raise ValueError('Guardian authority confirmation required for a minor')
        return self

class FactorScore(BaseModel):
    parent_factor:str=Field(min_length=2,max_length=120)
    sub_factor:str=Field(min_length=2,max_length=120)
    unit:str
    value:float=Field(allow_inf_nan=False)
    @model_validator(mode='after')
    def unit_bounds(self):
        if self.unit not in METRIC_UNITS:raise ValueError('Unsupported scoring unit')
        if self.unit=='Percentile' and not 0<=self.value<=100:raise ValueError('Percentile must be 0–100')
        if self.unit=='Sten' and not 1<=self.value<=10:raise ValueError('Sten must be 1–10')
        return self

class AssessmentIn(BaseModel):
    athlete_id:int
    case_id:int
    instrument_name:str=Field(min_length=2,max_length=180)
    instrument_version:str=Field(default='offline custom',max_length=50)
    observed_on:date
    note:str=Field(default='',max_length=500)
    scores:list[FactorScore]=Field(min_length=1,max_length=60)

class InjuryIn(BaseModel):
    athlete_id:int
    case_id:int
    injury_label:str=Field(min_length=2,max_length=140)
    start_date:date
    stage:str='early rehabilitation'

class InjuryCheckInIn(BaseModel):
    observed_on:date
    pain:float=Field(ge=0,le=10)
    fear_of_reinjury:float=Field(ge=0,le=10)
    confidence:float=Field(ge=0,le=10)
    coping:float=Field(ge=0,le=10)
    note:str=Field(default='',max_length=1000)

class ConsentIn(BaseModel):
    accepted:bool

class ReviewIn(BaseModel):
    time_offset_seconds:int=Field(ge=0,le=14400)
    kind:str='practitioner_observation'
    note:str=Field(min_length=2,max_length=1500)

class AdminTierIn(BaseModel):
    tier:str

class PracticeProfileIn(BaseModel):
    qualifications:str=Field(default='',max_length=1000)
    experience_years:int=Field(default=0,ge=0,le=70)
    achievements:str=Field(default='',max_length=1500)
    specialisms:str=Field(default='',max_length=250)


def calc_split(amount_paise:int,commission_bps:int):
    """Integer-paise calculation. Platform fee rounds half-up to nearest paise."""
    if amount_paise<=0 or not 0<=commission_bps<=10000:raise ValueError('Invalid amount or commission')
    platform=(amount_paise*commission_bps+5000)//10000
    return amount_paise-platform,platform


def register_beta_routes(app,db_session,get_current,athlete_for,case_for,event,tenant_public,serialize):
    # Mutations check CSRF. Only live Pro-specific features require a paid entitlement.
    def tier_required(level):
        def dep(user:Practitioner=Depends(get_current)):
            if not free_access.enabled() and VALID_TIERS.get(user.tier,0)<VALID_TIERS[level]:raise HTTPException(403,f'{level.title()} tier or higher required')
            return user
        return dep
    pro=tier_required('pro')

    def booking_for(db,user,bid):
        b=db.scalar(select(Booking).where(Booking.id==bid,Booking.tenant_id==user.tenant_id,Booking.practitioner_id==user.id))
        if not b:raise HTTPException(404,'Booking not found')
        return b

    def listed_rows(db):
        """Public, listing-eligible practitioners, owner first. On the restricted PostgreSQL
        roles an anonymous visitor has no table access, so the read goes through a narrow
        SECURITY DEFINER function (migration 0029); other engines use the ORM."""
        from .owner_profile import is_featured_owner
        from .hosted_profile import rls_profile
        t=tenant_public(db)
        if db.get_bind().dialect.name=='postgresql' and rls_profile():
            rows=db.execute(text('SELECT * FROM spps_dev.public_practitioner_directory(:slug)'),{'slug':t.slug}).mappings().all()
            return [dict(r) for r in rows]
        matches=db.execute(select(Practitioner,PractitionerProfile).join(PractitionerProfile,PractitionerProfile.practitioner_id==Practitioner.id).where(Practitioner.tenant_id==t.id,Practitioner.active==True,PractitionerProfile.public_approved==True)).all()
        matches=[(p,pr) for p,pr in matches if listing_eligible(db,p,pr)]
        # One verified, listed founder profile is pinned first; all other
        # approved practitioners retain stable ID order, not arbitrary DB order.
        matches.sort(key=lambda pair:(0 if is_featured_owner(*pair) else 1,pair[0].id))
        return [{'id':p.id,'name':p.name,'qualifications':pr.qualifications,'experience_years':pr.experience_years,
                 'achievements':pr.achievements,'specialisms':pr.specialisms,'avatar_key':pr.avatar_key,
                 'has_photo':bool(pr.avatar_bytes),'credentials_verified':pr.credentials_verified,
                 'consultation_fee_paise':pr.consultation_fee_paise,
                 'featured_owner':bool(is_featured_owner(p,pr))} for p,pr in matches]

    @app.get('/api/public/practitioners')
    def directory(db:Session=Depends(db_session)):
        from .owner_profile import OWNER_ROLE, OWNER_PHOTO_URL
        rows=listed_rows(db)
        # Explicit allowlist. No login email, phone, private account, linked account,
        # case data, client information or raw ORM objects enter the public response.
        # Only the verified owner uses the pinned official WinMindPerform image;
        # other profile photos are sanitized/served locally, never user URLs.
        # A practitioner without an uploaded photo gets a neutral placeholder. The stored avatar_key is
        # never turned into a URL.
        return [{
            'id':r['id'],
            'name':redact_public_profile(r['name']),
            'qualifications':redact_public_profile(r['qualifications']),
            'experience_years':r['experience_years'],
            'achievements':redact_public_profile(r['achievements']),
            'specialisms':redact_public_profile(r['specialisms']),
            'photo_url':OWNER_PHOTO_URL if r['featured_owner'] else (f"/api/public/practitioners/{r['id']}/photo" if r['has_photo'] else '/assets/avatars/placeholder.svg'),
            'featured_owner':bool(r['featured_owner']),
            'role':OWNER_ROLE if r['featured_owner'] else '',
            'consultation_fee_paise':0 if free_access.enabled() else r.get('consultation_fee_paise',0),
            'credentials_verified':r['credentials_verified'],
        } for r in rows]

    @app.get('/api/public/practitioners/{practitioner_id}/photo')
    def public_photo(practitioner_id:int,db:Session=Depends(db_session)):
        from fastapi import Response
        from .hosted_profile import rls_profile
        if practitioner_id not in {r['id'] for r in listed_rows(db)}:raise HTTPException(404,'Photo unavailable')
        if db.get_bind().dialect.name=='postgresql' and rls_profile():
            data=db.execute(text('SELECT spps_dev.public_practitioner_photo(:slug,:pid)'),{'slug':tenant_public(db).slug,'pid':practitioner_id}).scalar()
        else:
            profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==practitioner_id))
            data=profile.avatar_bytes if profile else None
        if not data:raise HTTPException(404,'Photo unavailable')
        return Response(content=bytes(data),media_type='image/webp',headers={'Cache-Control':'public, max-age=3600'})

    @app.get('/api/public/practitioners/{practitioner_id}/slots')
    def public_slots(practitioner_id:int,db:Session=Depends(db_session)):
        allowed={r['id'] for r in listed_rows(db)}
        if practitioner_id not in allowed:raise HTTPException(404,'Practitioner unavailable')
        if free_access.enabled():return []  # free v1.0 has no public slot booking
        booked=set(db.scalars(select(Booking.slot_id).where(Booking.status.in_(['awaiting_payment','paid_pending_review','approved','completed','refund_required']))).all())
        slots=db.scalars(select(AppointmentSlot).where(AppointmentSlot.practitioner_id==practitioner_id,AppointmentSlot.available==True).order_by(AppointmentSlot.starts_at)).all()
        return [{'id':s.id,'starts_at':s.starts_at,'duration_minutes':s.duration_minutes,'amount_paise':s.amount_paise,'currency':s.currency} for s in slots if s.id not in booked and (s.starts_at.replace(tzinfo=timezone.utc) if s.starts_at.tzinfo is None else s.starts_at)>now()]

    @app.get('/api/slots')
    def own_slots(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        # Exposes only the current practitioner's slots and their availability.
        slots=db.scalars(select(AppointmentSlot).where(
            AppointmentSlot.practitioner_id==user.id
        ).order_by(AppointmentSlot.starts_at.desc()).limit(100)).all()
        if not slots:return []
        active_slots=set(db.scalars(select(Booking.slot_id).where(
            Booking.practitioner_id==user.id,
            Booking.tenant_id==user.tenant_id,
            Booking.slot_id.in_([slot.id for slot in slots]),
            Booking.status.in_(['awaiting_payment','paid_pending_review','approved','completed','refund_required'])
        )).all())
        # Stage 13B: approval-first platform bookings also hold slots.
        held=getattr(app.state,'platform_held_slots',None)
        if held:active_slots|=set(held(user.id))
        current=now()
        result=[]
        for slot in slots:
            start=slot.starts_at.replace(tzinfo=timezone.utc) if slot.starts_at.tzinfo is None else slot.starts_at
            status='reserved' if slot.id in active_slots else ('inactive' if not slot.available else ('expired' if start<=current else 'available'))
            result.append({'id':slot.id,'starts_at':slot.starts_at,
                'duration_minutes':slot.duration_minutes,'amount_paise':slot.amount_paise,
                'currency':slot.currency,'status':status})
        return result

    @app.post('/api/slots',status_code=201)
    def create_slot(body:SlotIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if free_access.enabled() and body.amount_paise!=0:raise HTTPException(422,'Slots in SPPS Version 1.0 - Free must have zero fee')
        if not free_access.enabled() and body.amount_paise<10000:raise HTTPException(422,'Invalid consultation fee')
        if body.starts_at.tzinfo is None or body.starts_at.utcoffset() is None:raise HTTPException(422,'Appointment time must include an explicit UTC offset')
        if body.starts_at.astimezone(timezone.utc)<=now():raise HTTPException(422,'Slot must be in the future')
        slot=AppointmentSlot(practitioner_id=user.id,starts_at=body.starts_at.astimezone(timezone.utc),duration_minutes=body.duration_minutes,amount_paise=body.amount_paise)
        db.add(slot);db.flush();event(db,user,'slot.created','slot',slot.id);db.commit();return {'id':slot.id}

    @app.post('/api/public/bookings',status_code=201)
    def create_booking(body:BookIn,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Use the authenticated athlete/guardian booking portal in SPPS Version 1.0 - Free')
        t=tenant_public(db)
        slot=db.get(AppointmentSlot,body.slot_id)
        if not slot or not slot.available:raise HTTPException(404,'Slot unavailable')
        practitioner=db.get(Practitioner,slot.practitioner_id)
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==slot.practitioner_id))
        if not practitioner or practitioner.tenant_id!=t.id or not listing_eligible(db,practitioner,profile):raise HTTPException(404,'Slot unavailable')
        if (slot.starts_at.replace(tzinfo=timezone.utc) if slot.starts_at.tzinfo is None else slot.starts_at)<=now():raise HTTPException(409,'Slot expired')
        conflict=db.scalar(select(Booking.id).where(Booking.slot_id==slot.id,Booking.status.in_(['awaiting_payment','paid_pending_review','approved','completed','refund_required'])))
        if conflict:raise HTTPException(409,'Slot already reserved')
        b=Booking(tenant_id=t.id,practitioner_id=practitioner.id,slot_id=slot.id,contact_name=body.contact_name,contact_email=body.contact_email.lower(),is_minor=body.is_minor,guardian_confirmed=body.guardian_confirmed,contact_consent=True,video_consent=body.video_consent,amount_paise=slot.amount_paise,commission_bps=3000)
        db.add(b);db.commit();db.refresh(b)
        return {'booking_id':b.id,'status':b.status,'amount_paise':b.amount_paise,'currency':'INR','payment_mode':'requires_configured_gateway','message':'Booking requested; not confirmed until verified payment and practitioner approval.'}

    def provider_request(method,path,payload=None):
        key=os.getenv('RAZORPAY_KEY_ID','');secret=os.getenv('RAZORPAY_KEY_SECRET','')
        if not key or not secret:raise HTTPException(503,'Payment provider is not configured')
        auth=base64.b64encode(f'{key}:{secret}'.encode()).decode()
        data=json.dumps(payload).encode() if payload is not None else None
        headers={'Authorization':f'Basic {auth}','Content-Type':'application/json'}
        req=UrlRequest('https://api.razorpay.com/v1'+path,data=data,method=method,headers=headers)
        try:
            with urlopen(req,timeout=12) as resp:return json.loads(resp.read())
        except HTTPError as err:raise HTTPException(502,f'Payment provider returned HTTP {err.code}')
        except Exception:raise HTTPException(502,'Payment provider unavailable')

    @app.post('/api/public/bookings/{bid}/payment-order')
    def make_payment_order(bid:int,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Payment workflows are disabled in SPPS Version 1.0 - Free')
        b=db.get(Booking,bid)
        if not b or b.status!='awaiting_payment':raise HTTPException(404,'Booking not payable')
        if not os.getenv('RAZORPAY_KEY_ID') or not os.getenv('RAZORPAY_KEY_SECRET'):raise HTTPException(503,'Live payments are not enabled')
        if b.provider_order_id:return {'order_id':b.provider_order_id,'key_id':os.getenv('RAZORPAY_KEY_ID'),'amount_paise':b.amount_paise}
        order=provider_request('POST','/orders',{'amount':b.amount_paise,'currency':'INR','receipt':f'mc-book-{b.id}','notes':{'booking_id':str(b.id)}})
        if order.get('amount')!=b.amount_paise or order.get('currency')!='INR':raise HTTPException(502,'Provider order mismatch')
        b.provider_order_id=order['id'];db.commit()
        return {'order_id':b.provider_order_id,'key_id':os.getenv('RAZORPAY_KEY_ID'),'amount_paise':b.amount_paise}

    def confirm_captured(db,b,provider_payment_id):
        # No allocation or enquiry until the payment is verified with Razorpay.
        if b.status in ('paid_pending_review','approved'):
            old=db.scalar(select(PaymentAllocation).where(PaymentAllocation.booking_id==b.id))
            if old and old.provider_payment_id==provider_payment_id:return old
            raise HTTPException(409,'Booking has a different captured payment')
        if b.status!='awaiting_payment':raise HTTPException(409,'Booking is not payable')
        existing=db.scalar(select(PaymentAllocation).where(PaymentAllocation.provider_payment_id==provider_payment_id))
        if existing:raise HTTPException(409,'Payment already allocated')
        practitioner_share,platform_share=calc_split(b.amount_paise,b.commission_bps)
        record=PaymentAllocation(booking_id=b.id,provider_payment_id=provider_payment_id,gross_paise=b.amount_paise,practitioner_paise=practitioner_share,platform_paise=platform_share)
        b.provider_payment_id=provider_payment_id;b.status='paid_pending_review';db.add(record)
        # The enquiry appears only after verified capture. Never put clinical details in payments metadata.
        service=db.scalar(select(Service).where(Service.tenant_id==b.tenant_id).order_by(Service.id))
        if service:
            db.add(Inquiry(tenant_id=b.tenant_id,practitioner_id=b.practitioner_id,service_id=service.id,contact_name=b.contact_name,contact_email=b.contact_email,enquiring_for='my child / dependent' if b.is_minor else 'myself',sport='Not specified',support_topic='General enquiry',contact_preference='email',scheduling_note='Paid consultation booking – pending practitioner approval',consent_version='booking-v1',status='paid_pending_review'))
        db.flush();db.commit()
        return record

    def maybe_transfer(db,b,allocation):
        # Optional provider payout is NEVER silently represented as settled.
        if os.getenv('MODEL_C_ROUTE_AUTO_TRANSFER','false')!='true':return
        if allocation.payout_status!='allocation_only':return
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==b.practitioner_id))
        if not profile or not profile.linked_account_id:return
        # A captured payment can be transferred to a provider-approved linked account only.
        # Do not retry blindly after a network failure; reconcile with the payment provider first.
        try:
            transfer=provider_request('POST',f'/payments/{allocation.provider_payment_id}/transfers',{'transfers':[{'account':profile.linked_account_id,'amount':allocation.practitioner_paise,'currency':'INR'}]})
            items=transfer.get('items',[])
            if items and items[0].get('id'):
                allocation.provider_transfer_id=items[0]['id'];allocation.payout_status='transfer_requested'
            else:allocation.payout_status='transfer_needs_reconciliation'
        except HTTPException:
            allocation.payout_status='transfer_needs_reconciliation'
        db.commit()

    @app.post('/api/public/bookings/{bid}/verify-payment')
    def verify_payment(bid:int,payload:dict,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Payment workflows are disabled in SPPS Version 1.0 - Free')
        b=db.get(Booking,bid)
        if not b or not b.provider_order_id:raise HTTPException(404,'Booking payment order not found')
        pid=payload.get('razorpay_payment_id','');oid=payload.get('razorpay_order_id','');sig=payload.get('razorpay_signature','')
        if oid!=b.provider_order_id or not pid or not sig:raise HTTPException(403,'Invalid payment verification')
        secret=os.getenv('RAZORPAY_KEY_SECRET','')
        if not secret:raise HTTPException(503,'Payment provider not configured')
        expected=hmac.new(secret.encode(),f'{oid}|{pid}'.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,sig):raise HTTPException(403,'Invalid payment signature')
        p=provider_request('GET',f'/payments/{pid}')
        if p.get('status')!='captured' or p.get('order_id')!=oid or p.get('amount')!=b.amount_paise or p.get('currency')!='INR':raise HTTPException(409,'Captured payment not verified')
        a=confirm_captured(db,b,pid)
        maybe_transfer(db,b,a)
        return {'status':b.status,'allocation_status':a.payout_status,'practitioner_paise':a.practitioner_paise,'platform_paise':a.platform_paise}

    @app.post('/api/payment/razorpay/webhook')
    async def capture_webhook(request:Request,db:Session=Depends(db_session)):
        if free_access.enabled():raise HTTPException(410,'Payment workflows are disabled in SPPS Version 1.0 - Free')
        secret=os.getenv('RAZORPAY_WEBHOOK_SECRET','')
        if not secret:raise HTTPException(503,'Payment webhooks disabled')
        body=await request.body()
        if len(body)>50000:raise HTTPException(413,'Webhook exceeds size limit')
        signature=request.headers.get('x-razorpay-signature','')
        check=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(check,signature):raise HTTPException(403,'Invalid webhook signature')
        try:payload=json.loads(body)
        except Exception:raise HTTPException(400,'Invalid webhook body')
        if payload.get('event')!='payment.captured':return {'ignored':True}
        entity=payload.get('payload',{}).get('payment',{}).get('entity',{})
        pid=entity.get('id','');oid=entity.get('order_id','')
        if not pid or not oid:raise HTTPException(422,'Missing payment reference')
        b=db.scalar(select(Booking).where(Booking.provider_order_id==oid))
        if not b:raise HTTPException(404,'Booking order not found')
        confirmed=provider_request('GET',f'/payments/{pid}')
        if confirmed.get('status')!='captured' or confirmed.get('order_id')!=oid or confirmed.get('amount')!=b.amount_paise or confirmed.get('currency')!='INR':raise HTTPException(409,'Payment capture mismatch')
        a=confirm_captured(db,b,pid)
        maybe_transfer(db,b,a)
        return {'accepted':True,'allocation_status':a.payout_status}

    # Internal handles (verified-capture bookkeeping, auth dependency) for provider callbacks and the automated tests.
    app.state.payment_internals={'confirm_captured':confirm_captured,'get_current':get_current,'db_session':db_session}

    @app.get('/api/bookings')
    def bookings(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        values=db.scalars(select(Booking).where(Booking.practitioner_id==user.id,Booking.tenant_id==user.tenant_id).order_by(Booking.created_at.desc())).all()
        result=[]
        for b in values:
            room=db.scalar(select(ConsultationRoom).where(ConsultationRoom.booking_id==b.id))
            s=db.get(AppointmentSlot,b.slot_id)
            a=db.scalar(select(PaymentAllocation).where(PaymentAllocation.booking_id==b.id))
            result.append({'id':b.id,'contact_name':b.contact_name,'contact_email':b.contact_email,'starts_at':s.starts_at if s else None,'status':b.status,'room_id':room.id if room else None,'amount_paise':b.amount_paise,'allocation':{'practitioner_paise':a.practitioner_paise,'platform_paise':a.platform_paise,'payout_status':a.payout_status} if a else None})
        return result

    class PayoutAccountIn(BaseModel):
        linked_account_id:str=Field(min_length=6,max_length=100,pattern=r'^acc_[A-Za-z0-9]+$')

    @app.put('/api/admin/practitioners/{pid}/payout-account')
    def configure_payout_account(pid:int,body:PayoutAccountIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if not user.is_admin:raise HTTPException(403,'Administrator required')
        target=db.scalar(select(Practitioner).where(Practitioner.id==pid,Practitioner.tenant_id==user.tenant_id))
        if not target:raise HTTPException(404,'Practitioner not found')
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==pid))
        if not profile:raise HTTPException(409,'Verified public profile required first')
        # The operator must separately confirm the linked account was approved/KYC-verified by the provider.
        profile.linked_account_id=body.linked_account_id
        event(db,user,'payout_account.updated','practitioner',pid);db.commit()
        return {'updated':True,'provider_verification_required':True}

    @app.post('/api/bookings/{bid}/decline')
    def decline(bid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        b=booking_for(db,user,bid)
        if b.status!='paid_pending_review':raise HTTPException(409,'Cannot decline this booking status')
        b.status='refund_required'
        event(db,user,'booking.declined_refund_required','booking',b.id);db.commit()
        return {'status':b.status,'notice':'Refund must be processed and reconciled with the payment provider. No automatic refund has occurred.'}

    @app.post('/api/bookings/{bid}/approve')
    def approve(bid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        b=booking_for(db,user,bid)
        if b.status!='paid_pending_review':raise HTTPException(409,'Verified payment required before approval')
        b.status='approved'
        room=ConsultationRoom(booking_id=b.id)
        db.add(room);db.flush();event(db,user,'booking.approved','booking',b.id);db.commit()
        return {'status':b.status,'room_id':room.id,'video_enabled':b.video_consent}

    @app.post('/api/bookings/{bid}/complete')
    async def complete_consultation(bid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        """Owner-only finalization: explicitly end the appointment, revoke access.

        Ending a local camera preview is not equivalent to completing a consultation.
        This action is deliberately separate from WebRTC hang-up and cannot trigger
        automatically merely because a participant disconnects.
        """
        b=booking_for(db,user,bid)
        if b.status!='approved':raise HTTPException(409,'Only an approved consultation can be completed')
        room=db.scalar(select(ConsultationRoom).where(ConsultationRoom.booking_id==b.id))
        if not room or not room.active:raise HTTPException(409,'Active consultation room unavailable')
        b.status='completed'
        room.active=False
        for access in db.scalars(select(RoomAccess).where(RoomAccess.room_id==room.id)):
            access.revoked=True
        event(db,user,'booking.completed','booking',b.id)
        db.commit()
        # Close both sockets in this process, if connected. Horizontal scaling
        # requires a distributed room registry before launch (see deployment guide).
        for peer in tuple(peers.get(room.id,{}).values()):
            try:await peer.close(code=1000)
            except (WebSocketDisconnect,RuntimeError,OSError):pass
        return {'status':'completed','room_id':room.id,'room_active':False,'invite_tokens_revoked':True,
                'note':'Clinical session notes and financial payout remain separate workflows.'}

    @app.post('/api/bookings/{bid}/room-invite')
    def room_invite(bid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        b=booking_for(db,user,bid)
        if b.status!='approved' or not b.video_consent:raise HTTPException(403,'Approved booking and separate video consent required')
        room=db.scalar(select(ConsultationRoom).where(ConsultationRoom.booking_id==b.id,ConsultationRoom.active==True))
        if not room:raise HTTPException(404,'Room unavailable')
        # Short-lived bearer tokens. They must be delivered using a secure channel to the actual recipient.
        tokens={}
        for role in ('practitioner','guest'):
            token=new_token()
            db.add(RoomAccess(room_id=room.id,role=role,token_hash=hash_token(token),expires_at=now()+timedelta(hours=2)))
            tokens[role]=token
        event(db,user,'room.invites','room',room.id);db.commit()
        return {'room_id':room.id,'practitioner_token':tokens['practitioner'],'guest_token':tokens['guest'],'expires_in_minutes':120,'warning':'Links are bearer credentials. Share guest link only with verified booking contact.'}

    def access_for(db,room_id,token):
        item=db.scalar(select(RoomAccess).where(RoomAccess.room_id==room_id,RoomAccess.token_hash==hash_token(token),RoomAccess.revoked==False))
        if not item or is_expired(item.expires_at):raise HTTPException(403,'Invalid or expired room invitation')
        room=db.get(ConsultationRoom,room_id)
        b=db.get(Booking,room.booking_id) if room else None
        if not room or not room.active or not b or b.status!='approved' or not b.video_consent:raise HTTPException(403,'Consultation room unavailable')
        return item

    @app.get('/api/room/{room_id}/status')
    def room_status(room_id:int,access:str,db:Session=Depends(db_session)):
        a=access_for(db,room_id,access)
        return {'room_id':room_id,'role':a.role,'consented':bool(a.consent_at),'recording_enabled':False,'facial_emotion_analysis_enabled':False}

    @app.get('/api/room/{room_id}/ice')
    def ice_configuration(room_id:int,access:str,db:Session=Depends(db_session)):
        access_for(db,room_id,access)
        # Empty by default: no external STUN/TURN or IP metadata sharing (see server/ice_config.py).
        from .ice_config import IceConfigError,ice_config
        try:return ice_config('spps-'+secrets.token_hex(6),mode=os.getenv('MODEL_C_ENV','local').lower())
        except IceConfigError:raise HTTPException(503,'Video relay is not configured correctly') from None

    @app.post('/api/room/{room_id}/consent')
    def room_consent(room_id:int,access:str,body:ConsentIn,db:Session=Depends(db_session)):
        a=access_for(db,room_id,access)
        if not body.accepted:raise HTTPException(422,'Explicit live video consent required')
        a.consent_at=now();db.commit();return {'consented':True,'recording_enabled':False}

    @app.post('/api/rooms/{room_id}/reviews',status_code=201)
    def note_room(room_id:int,body:ReviewIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        room=db.get(ConsultationRoom,room_id)
        b=booking_for(db,user,room.booking_id) if room else None
        if not b or b.status!='approved':raise HTTPException(404,'Session not found')
        if body.kind not in ('practitioner_observation','client_self_report'):raise HTTPException(422,'Unsupported observation type')
        z=SessionReview(room_id=room_id,author_id=user.id,time_offset_seconds=body.time_offset_seconds,kind=body.kind,note=body.note)
        db.add(z);db.flush();event(db,user,'room.review','room',room.id);db.commit();return {'id':z.id}

    @app.get('/api/rooms/{room_id}/reviews')
    def read_room_reviews(room_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        room=db.get(ConsultationRoom,room_id)
        if not room:raise HTTPException(404,'Session not found')
        booking_for(db,user,room.booking_id)
        return [{'time_offset_seconds':z.time_offset_seconds,'kind':z.kind,'note':z.note} for z in db.scalars(select(SessionReview).where(SessionReview.room_id==room_id).order_by(SessionReview.time_offset_seconds))]

    # Two-peer WebRTC signaling only. No video frames, audio or facial metrics are processed on the server.
    peers=defaultdict(dict)
    @app.websocket('/ws/room/{room_id}')
    async def room_signal(websocket:WebSocket,room_id:int):
        # Legacy clinical rooms are outside both restricted RLS profiles (local milestone and hosted staging).
        if (os.getenv('MODEL_C_INTEGRATION_DEV','false').lower()=='true' or
                os.getenv('SPPS_HOSTED_RLS','false').lower()=='true'):
            await websocket.close(code=1008);return
        if (os.getenv('MODEL_C_ENV','local')=='staging' and
            os.getenv('MODEL_C_HOSTED_PILOT_UNLOCKED','false').lower()!='true'):
            await websocket.close(code=1008);return
        if os.getenv('MODEL_C_WEBRTC_ENABLED','false')!='true':
            await websocket.close(code=1008);return
        token=websocket.query_params.get('access','')
        with app.state.DB() as db:
            try:
                access=access_for(db,room_id,token)
                if not access.consent_at:raise HTTPException(403,'Consent required')
                role=access.role
            except HTTPException:
                await websocket.close(code=1008);return
        # Reserve the role before awaiting accept(). Otherwise two concurrent
        # connections can both pass the duplicate check and replace each other.
        if role in peers[room_id]:
            await websocket.close(code=1008)
            return
        peers[room_id][role]=websocket
        try:
            await websocket.accept()
            await websocket.send_json({'type':'ready','role':role})
            other=peers[room_id].get('guest' if role=='practitioner' else 'practitioner')
            if other:
                await websocket.send_json({'type':'peer-ready'})
                await other.send_json({'type':'peer-ready'})
            while True:
                message=await websocket.receive_json()
                if not isinstance(message,dict) or message.get('type') not in ('offer','answer','ice'):continue
                if len(json.dumps(message))>35000:continue
                other=peers[room_id].get('guest' if role=='practitioner' else 'practitioner')
                if other:await other.send_json(message)
        except (WebSocketDisconnect,RuntimeError):pass
        finally:
            if peers[room_id].get(role) is websocket:
                peers[room_id].pop(role,None)
                other=peers[room_id].get('guest' if role=='practitioner' else 'practitioner')
                if other:
                    try:
                        await other.send_json({'type':'peer-left'})
                    except (WebSocketDisconnect,RuntimeError,OSError):
                        pass
            if not peers[room_id]:peers.pop(room_id,None)

    @app.post('/api/assessments/offline',status_code=201)
    def assessment(body:AssessmentIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,body.athlete_id);c=case_for(db,user,body.case_id)
        if c.athlete_id!=a.id:raise HTTPException(400,'Mismatched case and athlete')
        run=AssessmentAdministration(tenant_id=user.tenant_id,athlete_id=a.id,case_id=c.id,instrument_name=body.instrument_name,instrument_version=body.instrument_version,observed_on=body.observed_on.isoformat(),note=body.note)
        db.add(run);db.flush()
        for score in body.scores:db.add(AssessmentSubscore(administration_id=run.id,**score.model_dump()))
        event(db,user,'assessment.offline','assessment',run.id);db.commit();return {'id':run.id,'score_count':len(body.scores)}

    @app.get('/api/athletes/{athlete_id}/assessments/offline')
    def assessments(athlete_id:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        athlete_for(db,user,athlete_id)
        runs=db.scalars(select(AssessmentAdministration).where(AssessmentAdministration.tenant_id==user.tenant_id,AssessmentAdministration.athlete_id==athlete_id).order_by(AssessmentAdministration.observed_on)).all()
        out=[]
        for run in runs:
            scores=db.scalars(select(AssessmentSubscore).where(AssessmentSubscore.administration_id==run.id)).all()
            out.append({'id':run.id,'instrument_name':run.instrument_name,'instrument_version':run.instrument_version,'observed_on':run.observed_on,'scores':[{'parent_factor':z.parent_factor,'sub_factor':z.sub_factor,'unit':z.unit,'value':z.value} for z in scores]})
        return out

    @app.post('/api/injury/episodes',status_code=201)
    def injury_episode(body:InjuryIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        a=athlete_for(db,user,body.athlete_id);c=case_for(db,user,body.case_id)
        if c.athlete_id!=a.id:raise HTTPException(400,'Mismatched case')
        if body.stage not in STAGES:raise HTTPException(422,'Invalid stage')
        z=InjuryEpisode(tenant_id=user.tenant_id,athlete_id=a.id,case_id=c.id,injury_label=body.injury_label,start_date=body.start_date.isoformat(),stage=body.stage)
        db.add(z);db.flush();event(db,user,'injury.created','injury',z.id);db.commit();return {'id':z.id}

    @app.post('/api/injury/episodes/{eid}/checkins',status_code=201)
    def injury_checkin(eid:int,body:InjuryCheckInIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        episode=db.scalar(select(InjuryEpisode).where(InjuryEpisode.id==eid,InjuryEpisode.tenant_id==user.tenant_id))
        if not episode:raise HTTPException(404,'Injury episode not found')
        athlete_for(db,user,episode.athlete_id)
        z=InjuryCheckIn(episode_id=eid,observed_on=body.observed_on.isoformat(),pain=body.pain,fear_of_reinjury=body.fear_of_reinjury,confidence=body.confidence,coping=body.coping,note=body.note)
        db.add(z);db.flush();event(db,user,'injury.checkin','injury',eid);db.commit();return {'id':z.id}

    @app.get('/api/athletes/{aid}/injury')
    def injury_history(aid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        athlete_for(db,user,aid)
        out=[]
        for e in db.scalars(select(InjuryEpisode).where(InjuryEpisode.athlete_id==aid,InjuryEpisode.tenant_id==user.tenant_id)):
            obs=db.scalars(select(InjuryCheckIn).where(InjuryCheckIn.episode_id==e.id).order_by(InjuryCheckIn.observed_on)).all()
            out.append({'id':e.id,'injury_label':e.injury_label,'stage':e.stage,'start_date':e.start_date,'checkins':[{'observed_on':o.observed_on,'pain':o.pain,'fear_of_reinjury':o.fear_of_reinjury,'confidence':o.confidence,'coping':o.coping,'note':o.note} for o in obs]})
        return out

    @app.get('/api/athletes/{aid}/indices')
    def indices(aid:int,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        athlete_for(db,user,aid)
        values=db.scalars(select(Observation).where(Observation.athlete_id==aid,Observation.tenant_id==user.tenant_id).order_by(Observation.observed_on)).all()
        by_date={}
        for obs in values:
            factor=db.get(Factor,obs.factor_id)
            if not factor or factor.kind!='monitoring' or factor.high<=factor.low:continue
            value=(obs.value-factor.low)/(factor.high-factor.low)*100
            k=factor.label.strip().lower()
            if k in ('training load','load','session rpe','fatigue','stress','sleep quality','recovery','energy','confidence'):
                by_date.setdefault(obs.observed_on,{})[k]=round(value,2)
        result=[]
        for d,v in sorted(by_date.items()):
            load=[v[k] for k in ('training load','load','session rpe','fatigue','stress') if k in v]
            recover=[v[k] if k not in ('fatigue','stress') else 100-v[k] for k in ('sleep quality','recovery','energy','confidence','fatigue','stress') if k in v]
            result.append({'date':d,'load_index':round(sum(load)/len(load),1) if load else None,'recovery_index':round(sum(recover)/len(recover),1) if recover else None,'load_inputs':len(load),'recovery_inputs':len(recover)})
        return {'series':result,'method':'Illustrative equal-weight normalised index of available practitioner-selected inputs. Not a validated diagnostic, fatigue, burnout or return-to-sport metric. Missing dates omitted and no automatic alert.'}

    @app.get('/api/account/tier')
    def tier(user:Practitioner=Depends(get_current)):
        if free_access.enabled():return free_access.free_tier_response()
        return {'tier':user.tier,'prices_inr_per_month':{'core':199,'pro':499},'features':{'athletes':True,'directory':True,'bookings':True,'video':True,'report_drafts':True,'reports':True,'report_exports':user.tier=='pro','ai':False,'ai_eligible':user.tier=='pro'},'ai_note':'AI is planned for Pro, but is not operational. No facial-expression or emotion inference is provided, and Pro eligibility does not activate unavailable features.', 'test_payment_enabled':False}

    @app.post('/api/admin/practitioners/{pid}/tier')
    def change_tier(pid:int,body:AdminTierIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if not user.is_admin:raise HTTPException(403,'Admin required')
        p=db.scalar(select(Practitioner).where(Practitioner.id==pid,Practitioner.tenant_id==user.tenant_id))
        if not p:raise HTTPException(404,'Practitioner not found')
        if free_access.enabled():raise HTTPException(410,'Paid plan upgrades are retired in SPPS Version 1.0 - Free')
        if body.tier not in VALID_TIERS:raise HTTPException(422,'Invalid tier')
        raise HTTPException(409,'Plan changes require a verified recurring subscription; admin role cannot grant paid entitlements directly')

    @app.get('/api/practitioner/profile')
    def private_profile(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        p=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
        return {'qualifications':p.qualifications,'experience_years':p.experience_years,'achievements':p.achievements,'specialisms':p.specialisms,'public_approved':p.public_approved} if p else {}

    @app.put('/api/practitioner/profile')
    def change_profile(body:PracticeProfileIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if db.get_bind().dialect.name=='postgresql':
            # Restricted app roles cannot UPDATE approval flags (or professional
            # credentials) directly. The owner-only SECURITY DEFINER routine
            # updates this identity's profile and revokes all publication approval.
            edited=db.execute(text("""
                SELECT spps_dev.update_own_practitioner_profile(
                    :qualifications,:experience_years,:achievements,:specialisms
                )
            """),body.model_dump()).scalar_one()
            if edited!=user.id:raise HTTPException(403,'Profile ownership mismatch')
        else:
            p=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
            if not p:p=PractitionerProfile(practitioner_id=user.id,public_approved=False);db.add(p)
            for k,v in body.model_dump().items():setattr(p,k,v)
            if not user.is_admin:
                p.public_approved=False
                p.credentials_verified=False
        event(db,user,'profile.updated','practitioner',user.id)
        db.commit()
        # The verified platform owner is the reviewer: the owner's own listing stays published.
        return {'review_required':not user.is_admin}

