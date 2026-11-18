"""Marketplace enquiry-only expansion. Does not charge, book, or settle packages.
Institution requests are admin-only; no clinical data are collected here.
"""
from __future__ import annotations
import time
from collections import defaultdict,deque
from threading import Lock
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Practitioner, PractitionerProfile, PractitionerProgramme, ProgrammeEnquiry, OrganisationQuoteRequest
from .subscription_routes import active_membership,listing_eligible

class ProgrammeIn(BaseModel):
    title:str=Field(min_length=4,max_length=120)
    duration_weeks:int=Field(ge=1,le=52)
    sessions_included:int=Field(ge=1,le=50)
    deliverables:str=Field(min_length=12,max_length=1400)
    amount_paise:int=Field(ge=10000,le=100000000)

class ProgrammeEnquiryIn(BaseModel):
    programme_id:int
    contact_name:str=Field(min_length=2,max_length=120)
    contact_email:EmailStr
    message:str=Field(default='',max_length=350)
    contact_consent:bool
    is_minor:bool=False
    guardian_confirmed:bool=False
    website:str=Field(default='',max_length=100)

class OrganisationIn(BaseModel):
    organisation_name:str=Field(min_length=3,max_length=160)
    contact_name:str=Field(min_length=2,max_length=120)
    contact_email:EmailStr
    practitioner_seats:int=Field(ge=1,le=500)
    athletes_estimate:int=Field(ge=0,le=100000)
    requirements:str=Field(default='',max_length=700)
    contact_consent:bool
    website:str=Field(default='',max_length=100)

class StatusUpdate(BaseModel):
    status:str

def register_marketplace_routes(app, db_session, get_current, tenant_public, event):
    attempts=defaultdict(deque)
    lock=Lock()
    def throttle(request:Request):
        from .hosted_profile import client_ip
        ip=client_ip(request)
        tick=time.monotonic()
        with lock:
            q=attempts[ip]
            while q and tick-q[0]>3600:q.popleft()
            if len(q)>=12:raise HTTPException(429,'Too many requests')
            q.append(tick)

    def visible(db):
        tenant=tenant_public(db)
        rows=db.execute(select(Practitioner,PractitionerProfile).join(PractitionerProfile,PractitionerProfile.practitioner_id==Practitioner.id).where(Practitioner.tenant_id==tenant.id,Practitioner.active==True,PractitionerProfile.public_approved==True)).all()
        return {u.id for u,profile in rows if listing_eligible(db,u,profile)}

    def own_programme(db,user,pid):
        record=db.scalar(select(PractitionerProgramme).where(PractitionerProgramme.id==pid,PractitionerProgramme.practitioner_id==user.id,PractitionerProgramme.tenant_id==user.tenant_id))
        if not record:raise HTTPException(404,'Programme not found')
        return record

    def pack(p):
        return {'id':p.id,'practitioner_id':p.practitioner_id,'title':p.title,'duration_weeks':p.duration_weeks,'sessions_included':p.sessions_included,'deliverables':p.deliverables,'amount_paise':p.amount_paise,'currency':'INR','published':p.published}

    @app.get('/api/public/programmes')
    def public_programmes(db:Session=Depends(db_session)):
        # Paid programmes are retired in free v1.0; an anonymous visitor has no table access on hosted roles.
        from . import free_access
        if free_access.enabled():return []
        ids=visible(db)
        return [pack(p) for p in db.scalars(select(PractitionerProgramme).where(PractitionerProgramme.practitioner_id.in_(ids),PractitionerProgramme.published==True).order_by(PractitionerProgramme.id.desc())).all()]

    @app.get('/api/practitioner/programmes')
    def own_programmes(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        return [pack(p) for p in db.scalars(select(PractitionerProgramme).where(PractitionerProgramme.tenant_id==user.tenant_id,PractitionerProgramme.practitioner_id==user.id).order_by(PractitionerProgramme.id.desc())).all()]

    @app.post('/api/practitioner/programmes',status_code=201)
    def create_programme(body:ProgrammeIn,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        item=PractitionerProgramme(tenant_id=user.tenant_id,practitioner_id=user.id,title=body.title.strip(),duration_weeks=body.duration_weeks,sessions_included=body.sessions_included,deliverables=body.deliverables.strip(),amount_paise=body.amount_paise,published=True)
        db.add(item);db.flush();event(db,user,'programme.created','programme',item.id);db.commit()
        return pack(item)

    @app.patch('/api/practitioner/programmes/{pid}')
    def toggle_programme(pid:int,body:StatusUpdate,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if body.status not in ('published','hidden'):raise HTTPException(422,'Choose published or hidden')
        item=own_programme(db,user,pid);item.published=body.status=='published'
        event(db,user,'programme.visibility','programme',pid);db.commit();return pack(item)

    @app.post('/api/public/programme-enquiries',status_code=201)
    def request_programme(payload:ProgrammeEnquiryIn,request:Request,db:Session=Depends(db_session)):
        if payload.website:return {'accepted':True,'reference':'Received','payment_status':'not_charged'}
        throttle(request)
        if not payload.contact_consent or (payload.is_minor and not payload.guardian_confirmed):raise HTTPException(422,'Required contact/guardian consent was not provided')
        programme=db.get(PractitionerProgramme,payload.programme_id)
        if not programme or not programme.published or programme.practitioner_id not in visible(db):raise HTTPException(404,'Programme not available')
        record=ProgrammeEnquiry(tenant_id=programme.tenant_id,practitioner_id=programme.practitioner_id,programme_id=programme.id,contact_name=payload.contact_name.strip(),contact_email=str(payload.contact_email).lower(),message=payload.message.strip(),quoted_amount_paise=programme.amount_paise)
        db.add(record);db.commit();db.refresh(record)
        return {'accepted':True,'reference':f'PR-{record.id:06d}','payment_status':'not_charged','message':'Request delivered to the practitioner inbox. No payment or session has been booked.'}

    @app.get('/api/practitioner/programme-enquiries')
    def own_requests(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        entries=db.scalars(select(ProgrammeEnquiry).where(ProgrammeEnquiry.tenant_id==user.tenant_id,ProgrammeEnquiry.practitioner_id==user.id).order_by(ProgrammeEnquiry.id.desc())).all()
        return [{'id':e.id,'programme_id':e.programme_id,'contact_name':e.contact_name,'contact_email':e.contact_email,'message':e.message,'quoted_amount_paise':e.quoted_amount_paise,'status':e.status,'created_at':e.created_at} for e in entries]

    @app.patch('/api/practitioner/programme-enquiries/{eid}')
    def update_request(eid:int,body:StatusUpdate,user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if body.status not in ('contacted','closed'):raise HTTPException(422,'Choose contacted or closed')
        req=db.scalar(select(ProgrammeEnquiry).where(ProgrammeEnquiry.id==eid,ProgrammeEnquiry.tenant_id==user.tenant_id,ProgrammeEnquiry.practitioner_id==user.id))
        if not req:raise HTTPException(404,'Request not found')
        req.status=body.status;event(db,user,'programme_request.updated','programme_enquiry',eid);db.commit()
        return {'id':eid,'status':req.status}

    @app.post('/api/public/organisation-quote',status_code=201)
    def quote(body:OrganisationIn,request:Request,db:Session=Depends(db_session)):
        if body.website:return {'accepted':True,'reference':'Received'}
        throttle(request)
        if not body.contact_consent:raise HTTPException(422,'Contact consent required')
        tenant=tenant_public(db)
        inquiry=OrganisationQuoteRequest(tenant_id=tenant.id,organisation_name=body.organisation_name.strip(),contact_name=body.contact_name.strip(),contact_email=str(body.contact_email).lower(),practitioner_seats=body.practitioner_seats,athletes_estimate=body.athletes_estimate,requirements=body.requirements.strip())
        db.add(inquiry);db.commit();db.refresh(inquiry)
        return {'accepted':True,'reference':f'ORG-{inquiry.id:06d}','notice':'Quotation request received. Annual pricing requires a separate agreement. No charge was made.'}

    @app.get('/api/admin/organisation-quotes')
    def organisation_inbox(user:Practitioner=Depends(get_current),db:Session=Depends(db_session)):
        if not user.is_admin:raise HTTPException(403,'Administrator required')
        records=db.scalars(select(OrganisationQuoteRequest).where(OrganisationQuoteRequest.tenant_id==user.tenant_id).order_by(OrganisationQuoteRequest.id.desc())).all()
        return [{'id':x.id,'organisation_name':x.organisation_name,'contact_name':x.contact_name,'contact_email':x.contact_email,'practitioner_seats':x.practitioner_seats,'athletes_estimate':x.athletes_estimate,'requirements':x.requirements,'status':x.status,'created_at':x.created_at} for x in records]
