"""Phase 8: self-serve athlete and guardian accounts (flag SPPS_SELF_SERVE_ENABLED; needs the athlete loop and the
directory switched on too).

1. A practitioner accepts a directory enquiry: an athlete record is created in their caseload and the phase 6
   invitation is sent (guardian first for under-18s).
2. An athlete or guardian may create an account first and send an enquiry from it. The practitioner sees only the
   enquiry itself; nothing else is shared until the practitioner accepts AND the athlete (guardian) accepts the
   invitation and its consent screen (phase 6 rules). Pending invitations are listed in the account, so the flow
   works without the e-mailed link, in any browser and in Chrome from the Android app.
3. Switching practitioner: the current link is ended (the old practitioner keeps what was shared up to then) and an
   enquiry goes to the new practitioner; a new link starts only when both sides accept.
4. Lifecycle without anyone else's consent: deactivate (sign-in blocked, reactivation by e-mail), export, delete.
   Deletion removes the account's identity data (account, sessions, invitations to its address, its enquiry
   copies), ends and severs every link and withdraws every report it approved.
5. Athlete-approved report links for coaches and clubs (also needs SPPS_REPORT_LINKS_ENABLED): the practitioner
   asks; the athlete (guardian for under-18s) sees the exact signed coach-level snapshot and approves or declines;
   approval mints an expiring link, no coach account. Withdrawing, revoking, expiry or the link ending close it.

Portal and public code here use only the identity-schema connections plus the same anonymous directory-enquiry
insert the public form uses; they never read clinical tables.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from ..models import Athlete, now
from ..security import verify_password
from . import flags, guards
from .athlete_loop import AcceptIn
from .context import Ctx
from .identity_models import (AthleteAccount, AthleteSession, LoopInvite, LoopLink, LoopRequest, LoopShareRequest,
                              LoopShareView)
from .models import AGE_GROUPS, AthleteProfile, DirectoryEnquiry, SharedReport

SHARE_AUDIENCES = ("coach",)


def share_hash(token: str) -> str:
    return hashlib.sha256(("spps-share:" + token).encode()).hexdigest()


def _aware(v):
    from datetime import timezone
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class AcceptEnquiryIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=2, max_length=32)
    age_group: str
    invite_email: EmailStr | None = None
    service_consent_confirmed: bool = False


class AccountEnquiryIn(BaseModel):
    practitioner_id: int
    sport: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=2, max_length=600)
    form_token: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=200)


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class DeleteIn(PasswordIn):
    confirm: str


class EmailIn(BaseModel):
    email: EmailStr


class ShareIn(BaseModel):
    recipient_label: str = Field(default="", max_length=120)
    expires_in_days: int = Field(default=14, ge=1, le=30)


class ApproveIn(BaseModel):
    recipient_email: EmailStr | None = None


def register(app: FastAPI, ctx: Ctx) -> None:
    from .athlete_loop import COOKIE, EXPORT_EXTRAS, base_url, send_mail, sha
    from .directory import EnquiryIn
    from .record import is_minor
    loop = app.state.v2_loop
    ident = loop.ident
    get_current, db_session = ctx.get_current, ctx.db_session
    portal_account = loop.portal_account

    # ------------------------------------------------------------------ 1. practitioner accepts an enquiry
    def own_enquiry(db, user, eid) -> DirectoryEnquiry:
        e = db.scalar(select(DirectoryEnquiry).where(DirectoryEnquiry.id == eid,
                                                     DirectoryEnquiry.practitioner_id == user.id,
                                                     DirectoryEnquiry.tenant_id == user.tenant_id))
        if not e:
            raise HTTPException(404, "Enquiry not found")
        if e.status in ("accepted", "declined"):
            raise HTTPException(409, "This enquiry has already been " + e.status)
        return e

    def set_request(ref, status, link_id=None):
        if not ref:
            return
        with ident.Auth() as a:
            r = a.scalar(select(LoopRequest).where(LoopRequest.ref == ref))
            if r and r.status == "sent":
                r.status, r.decided_at = status, now()
                if link_id:
                    r.link_id = link_id
                a.commit()

    @app.post("/api/v2/directory/enquiries/{eid}/accept", status_code=201)
    def accept_enquiry(eid: int, body: AcceptEnquiryIn, request: Request, user=Depends(get_current),
                       db=Depends(db_session)):
        e = own_enquiry(db, user, eid)
        if not body.service_consent_confirmed:
            raise HTTPException(422, "Confirm that you have agreed the service with the athlete (or guardian) first")
        if body.age_group not in AGE_GROUPS:
            raise HTTPException(422, "Choose an age group")
        minor = is_minor(body.age_group)
        if e.ref:
            # Account-first enquiry: the invitation goes to the account that sent it, and its role must fit.
            if (e.role == "parent") != minor:
                raise HTTPException(422, "This enquiry came from a " + ("guardian" if e.role == "parent" else "athlete")
                                    + " account; choose " + ("an under-18" if e.role == "parent" else "an adult")
                                    + " age group")
            email = e.contact
        else:
            email = str(body.invite_email or (e.contact if "@" in e.contact else "")).strip().lower()
        if not email:
            raise HTTPException(422, "Give the " + ("guardian's" if minor else "athlete's") + " e-mail address")
        if db.scalar(select(Athlete.id).where(Athlete.tenant_id == user.tenant_id, Athlete.owner_practitioner_id == user.id,
                                              Athlete.code == body.code.strip())):
            raise HTTPException(409, "Athlete code already exists")
        a = Athlete(tenant_id=user.tenant_id, owner_practitioner_id=user.id, code=body.code.strip(),
                    display_name=body.display_name.strip(), sport=e.sport, service_consent_recorded_at=now())
        db.add(a)
        db.flush()
        db.add(AthleteProfile(athlete_id=a.id, tenant_id=user.tenant_id, practitioner_id=user.id,
                              age_group=body.age_group, guardian_email=email if minor else "",
                              guardian_name=e.name if minor and e.role == "parent" else "",
                              referral_source="SPPS directory enquiry"))
        e.status, e.athlete_id = "accepted", a.id
        e.responded_at = e.responded_at or now()
        ctx.event(db, user, "enquiry.accepted", "athlete", a.id)
        db.commit()  # the record exists even if the e-mail fails; the practitioner can re-invite from it
        res = loop.create_invite(db, user, a, email, request)
        set_request(e.ref, "accepted", res["link_id"])
        return {"athlete_id": a.id, **res}

    @app.post("/api/v2/directory/enquiries/{eid}/decline")
    def decline_enquiry(eid: int, user=Depends(get_current), db=Depends(db_session)):
        e = own_enquiry(db, user, eid)
        e.status = "declined"
        e.responded_at = e.responded_at or now()
        db.commit()
        set_request(e.ref, "declined")
        return {"status": "declined"}

    # ------------------------------------------------------------------ 2. account-first enquiries
    def send_account_enquiry(acc, body: AccountEnquiryIn, request, db):
        with loop.adata(acc) as d:
            if d.scalar(select(LoopRequest.id).where(LoopRequest.account_id == acc.id, LoopRequest.status == "sent",
                                                     LoopRequest.practitioner_id == body.practitioner_id)):
                raise HTTPException(409, "You already have an open enquiry with this practitioner")
        ref = secrets.token_hex(16)
        res = app.state.v2_directory_enquiry(
            body.practitioner_id,
            EnquiryIn(name=acc.display_name, contact=acc.email, role="parent" if acc.role == "guardian" else "athlete",
                      sport=body.sport, message=body.message, form_token=body.form_token, website=body.website),
            request, db, account_id=acc.id, ref=ref)
        with loop.adata(acc) as d:
            r = LoopRequest(account_id=acc.id, tenant_id=res["tenant_id"], practitioner_id=body.practitioner_id,
                            practitioner_name=res["practitioner_name"], ref=ref, sport=body.sport.strip(),
                            message=body.message.strip(), status="sent")
            d.add(r)
            d.commit()
            return {"id": r.id, "status": "sent", "message": res["message"]}

    @app.post("/api/v2/portal/enquiries", status_code=201)
    def portal_enquiry(body: AccountEnquiryIn, request: Request, acc=Depends(portal_account), db=Depends(db_session)):
        return loop.wrap(loop.tenant_id_default(), send_account_enquiry(acc, body, request, db))

    def request_json(r):
        return {"id": r.id, "practitioner_id": r.practitioner_id, "practitioner_name": r.practitioner_name,
                "sport": r.sport, "message": r.message, "status": r.status, "created_at": r.created_at,
                "decided_at": r.decided_at}

    @app.get("/api/v2/portal/enquiries")
    def portal_enquiries(acc=Depends(portal_account)):
        with loop.adata(acc) as d:
            return loop.wrap(loop.tenant_id_default(), [request_json(r) for r in d.scalars(
                select(LoopRequest).where(LoopRequest.account_id == acc.id).order_by(LoopRequest.id.desc()))])

    @app.post("/api/v2/portal/enquiries/{rid}/withdraw")
    def portal_withdraw_enquiry(rid: int, acc=Depends(portal_account)):
        with loop.adata(acc) as d:
            r = d.scalar(select(LoopRequest).where(LoopRequest.id == rid, LoopRequest.account_id == acc.id))
            if not r:
                raise HTTPException(404, "Not found")
            if r.status == "sent":
                r.status, r.decided_at = "withdrawn", now()
                d.commit()
            return loop.wrap(r.tenant_id, request_json(r))

    def open_invites(a, acc):
        out = []
        for inv in a.scalars(select(LoopInvite).where(LoopInvite.email == acc.email, LoopInvite.kind == acc.role,
                                                      LoopInvite.used_at.is_(None), LoopInvite.revoked_at.is_(None))):
            l_ = a.get(LoopLink, inv.link_id)
            if _aware(inv.expires_at) >= now() and l_ and l_.status != "ended":
                out.append((inv, l_))
        return out

    @app.get("/api/v2/portal/invitations")
    def portal_invitations(acc=Depends(portal_account)):
        """Invitations addressed to this account, so it can accept without the e-mailed link."""
        with ident.Auth() as a:
            return loop.wrap(loop.tenant_id_default(), [
                {"id": inv.id, "kind": inv.kind, "practitioner_name": l_.practitioner_name,
                 "athlete_label": l_.athlete_label, "is_minor": l_.is_minor, "scope": json.loads(l_.scope),
                 "expires_at": inv.expires_at,
                 "guardian_sees": "participation and summaries your practitioner releases; your answers only if "
                                  "you and your practitioner both allow it"} for inv, l_ in open_invites(a, acc)])

    @app.post("/api/v2/portal/invitations/{iid}/accept")
    def portal_accept_invitation(iid: int, body: AcceptIn, request: Request, acc=Depends(portal_account)):
        guards.limit_ip(request, "invite-redeem")
        if not body.consent:
            raise HTTPException(422, "Consent is required to link with this practitioner")
        with ident.Auth() as a:
            inv = next((i for i, _ in open_invites(a, acc) if i.id == iid), None)
            if not inv:
                raise HTTPException(404, "This invitation has expired or was already used")
            return loop.accept_invite(a, inv, body, acc, request)

    # ------------------------------------------------------------------ 3. switch practitioner
    @app.post("/api/v2/portal/links/{link_id}/switch", status_code=201)
    def portal_switch(link_id: int, body: AccountEnquiryIn, request: Request, acc=Depends(portal_account),
                      db=Depends(db_session)):
        with loop.adata(acc) as d:
            l_ = loop.portal_link(d, acc, link_id)
            if l_.practitioner_id == body.practitioner_id:
                raise HTTPException(409, "Choose a different practitioner")
        sent = send_account_enquiry(acc, body, request, db)  # first, so a refused enquiry leaves the link open
        with loop.adata(acc) as d:
            l_ = loop.portal_link(d, acc, link_id)
            l_.status, l_.ended_at = "ended", now()
            l_.ended_by = "athlete" if l_.athlete_account_id == acc.id else "guardian"
            d.commit()
        return loop.wrap(loop.tenant_id_default(), {"ended_link": link_id, "enquiry": sent})

    # ------------------------------------------------------------------ 4. lifecycle
    def check_password(acc, password):
        if not verify_password(password, acc.password_hash):
            raise HTTPException(403, "Password is incorrect")

    @app.post("/api/v2/portal/account/deactivate")
    def portal_deactivate(body: PasswordIn, response: Response, acc=Depends(portal_account)):
        check_password(acc, body.password)
        with ident.Auth() as a:
            row = a.get(AthleteAccount, acc.id)
            row.status = "deactivated"
            a.query(AthleteSession).filter(AthleteSession.account_id == acc.id).update(
                {AthleteSession.revoked: True}, synchronize_session=False)
            a.commit()
        response.delete_cookie(COOKIE, path="/api/v2/portal")
        return {"status": "deactivated", "message": "Your account is switched off. Your practitioner links are "
                                                    "unchanged; reactivate from the sign-in page at any time."}

    @app.post("/api/v2/portal/auth/reactivate", status_code=202)
    def portal_reactivate(body: EmailIn, request: Request):
        guards.limit_ip(request, "portal-auth")
        guards.hit("portal-auth", guards.email_key(str(body.email)))
        email = str(body.email).lower()
        token = secrets.token_urlsafe(32)
        with ident.Auth() as a:
            acc = a.scalar(select(AthleteAccount).where(AthleteAccount.email == email))
            if acc and acc.status == "deactivated":
                acc.verify_token_hash, acc.verify_expires_at = sha(token), now() + timedelta(days=2)
                a.commit()
                send_mail(app, email, "Reactivate your SPPS account",
                          f"Open this link to switch your account back on (any browser or the app): "
                          f"{base_url(request)}/athlete?verify={token}")
        return {"accepted": True, "message": "If the account exists and is switched off, a link is on its way."}

    @app.post("/api/v2/portal/account/delete")
    def portal_delete(body: DeleteIn, request: Request, response: Response, acc=Depends(portal_account)):
        guards.limit_ip(request, "portal-auth")
        check_password(acc, body.password)
        if body.confirm != "DELETE":
            raise HTTPException(422, "Type DELETE to confirm")
        with ident.Auth() as a:
            links = a.scalars(select(LoopLink).where((LoopLink.athlete_account_id == acc.id)
                                                     | (LoopLink.guardian_account_id == acc.id))).all()
            for l_ in links:
                if l_.status != "ended":
                    l_.status, l_.ended_at, l_.ended_by = "ended", now(), "deleted"
                if l_.athlete_account_id == acc.id:
                    l_.athlete_account_id = None
                if l_.guardian_account_id == acc.id:
                    l_.guardian_account_id = None
                for s in a.scalars(select(LoopShareRequest).where(LoopShareRequest.link_id == l_.id,
                                                                  LoopShareRequest.status.in_(("pending", "approved")))):
                    s.status, s.decided_at = "withdrawn", now()
            a.flush()
            a.query(LoopInvite).filter(LoopInvite.email == acc.email).delete(synchronize_session=False)
            a.query(LoopRequest).filter(LoopRequest.account_id == acc.id).delete(synchronize_session=False)
            a.query(AthleteSession).filter(AthleteSession.account_id == acc.id).delete(synchronize_session=False)
            a.query(AthleteAccount).filter(AthleteAccount.id == acc.id).delete(synchronize_session=False)
            a.commit()
        response.delete_cookie(COOKIE, path="/api/v2/portal")
        return {"deleted": True, "links_ended": len(links),
                "message": "Your account and sign-in details are deleted and every practitioner link is ended. "
                           "Information you had already shared stays only in each practitioner's own record."}

    def export_requests(d, acc):
        return {"enquiries": [request_json(r) for r in d.scalars(
                    select(LoopRequest).where(LoopRequest.account_id == acc.id))],
                "report_sharing": [{"title": s.title, "audience": s.audience, "status": s.status,
                                    "decided_at": s.decided_at, "expires_at": s.expires_at}
                                   for s in d.scalars(select(LoopShareRequest).where(
                                       LoopShareRequest.decided_by_account == acc.id))]}
    EXPORT_EXTRAS[:] = [f for f in EXPORT_EXTRAS if getattr(f, "__name__", "") != "export_requests"]
    EXPORT_EXTRAS.append(export_requests)

    # ------------------------------------------------------------------ 5. athlete-approved report links
    def shares_on():
        if not flags.report_links_enabled():
            raise HTTPException(404, "Not found")

    def own_signed_report(db, user, rid) -> SharedReport:
        r = db.scalar(select(SharedReport).where(SharedReport.id == rid, SharedReport.practitioner_id == user.id,
                                                 SharedReport.tenant_id == user.tenant_id))
        if not r:
            raise HTTPException(404, "Report not found")
        if r.status != "signed" or not r.snapshot:
            raise HTTPException(409, "Sign the report first")
        if r.audience not in SHARE_AUDIENCES:
            raise HTTPException(409, "Only coach-level reports can be shared this way")
        return r

    def share_json(s, views=None):
        out = {"id": s.id, "report_id": s.report_id, "title": s.title, "audience": s.audience,
               "recipient_label": s.recipient_label, "status": s.status, "approver_role": s.approver_role,
               "decided_at": s.decided_at, "expires_at": s.expires_at, "created_at": s.created_at}
        if views is not None:
            out["views"] = views
        return out

    @app.post("/api/v2/v2-reports/{rid}/share-requests", status_code=201)
    def request_share(rid: int, body: ShareIn, request: Request, user=Depends(get_current), db=Depends(db_session)):
        shares_on()
        r = own_signed_report(db, user, rid)
        snap = r.snapshot
        with loop.pdata(user) as d:
            l_ = d.scalar(select(LoopLink).where(LoopLink.athlete_record_id == r.athlete_id,
                                                 LoopLink.practitioner_id == user.id, LoopLink.status == "active"))
            if not l_:
                raise HTTPException(409, "The athlete has no active link in the athlete app; share with a recorded "
                                         "consent instead")
            s = LoopShareRequest(link_id=l_.id, tenant_id=user.tenant_id, practitioner_id=user.id, report_id=r.id,
                                 audience=r.audience, title=r.title, recipient_label=body.recipient_label.strip(),
                                 snapshot=snap, approver_role="guardian" if l_.is_minor else "athlete",
                                 expires_in_days=body.expires_in_days, status="pending")
            d.add(s)
            d.commit()
            approver = l_.guardian_account_id if l_.is_minor else l_.athlete_account_id
            out = share_json(s)
        with ident.Auth() as a:
            who = a.get(AthleteAccount, approver) if approver else None
            to = who.email if who and who.status == "active" else ""
        if to:
            send_mail(app, to, "Your practitioner asks to share a report",
                      f"{user.name} would like to share \"{r.title}\" with your coach. Nothing is sent unless you "
                      f"approve it: {base_url(request)}/athlete")
        ctx.event(db, user, "report.share_requested", "shared_report", r.id)
        db.commit()
        return out

    @app.get("/api/v2/v2-reports/{rid}/share-requests")
    def list_shares(rid: int, user=Depends(get_current), db=Depends(db_session)):
        shares_on()
        # Someone else's report id answers exactly like an unknown one (no 200 with an empty list).
        if not db.scalar(select(SharedReport.id).where(SharedReport.id == rid, SharedReport.practitioner_id == user.id,
                                                       SharedReport.tenant_id == user.tenant_id)):
            raise HTTPException(404, "Report not found")
        with loop.pdata(user) as d:
            rows = d.scalars(select(LoopShareRequest).where(LoopShareRequest.report_id == rid,
                                                            LoopShareRequest.practitioner_id == user.id)
                             .order_by(LoopShareRequest.id.desc())).all()
            return [share_json(s, [{"at": v.viewed_at, "outcome": v.outcome} for v in d.scalars(
                select(LoopShareView).where(LoopShareView.share_id == s.id))]) for s in rows]

    @app.post("/api/v2/report-shares/{sid}/revoke")
    def revoke_share(sid: int, user=Depends(get_current)):
        shares_on()
        with loop.pdata(user) as d:
            s = d.scalar(select(LoopShareRequest).where(LoopShareRequest.id == sid,
                                                        LoopShareRequest.practitioner_id == user.id))
            if not s:
                raise HTTPException(404, "Not found")
            if s.status in ("pending", "approved"):
                s.status = "revoked"
                d.commit()
            return share_json(s)

    def my_share(d, acc, sid) -> LoopShareRequest:
        s = d.get(LoopShareRequest, sid)
        if not s:
            raise HTTPException(404, "Not found")
        l_ = loop.portal_link(d, acc, s.link_id)  # 404 when ended or not this account's
        mine = (s.approver_role == "athlete" and l_.athlete_account_id == acc.id and l_.status == "active") or \
               (s.approver_role == "guardian" and l_.guardian_account_id == acc.id)
        if not mine:
            raise HTTPException(404, "Not found")
        return s

    @app.get("/api/v2/portal/report-shares")
    def portal_shares(acc=Depends(portal_account)):
        shares_on()
        with loop.adata(acc) as d:
            out = []
            links = d.scalars(select(LoopLink).where((LoopLink.athlete_account_id == acc.id)
                                                     | (LoopLink.guardian_account_id == acc.id))).all()
            for l_ in links:
                for s in d.scalars(select(LoopShareRequest).where(LoopShareRequest.link_id == l_.id)
                                   .order_by(LoopShareRequest.id.desc())):
                    try:
                        my_share(d, acc, s.id)
                    except HTTPException:
                        continue
                    out.append({**share_json(s), "practitioner_name": l_.practitioner_name,
                                "athlete_label": l_.athlete_label,
                                "content": json.loads(s.snapshot) if s.status == "pending" else None})
            return loop.wrap(loop.tenant_id_default(), out)

    @app.post("/api/v2/portal/report-shares/{sid}/approve")
    def portal_approve_share(sid: int, body: ApproveIn, request: Request, acc=Depends(portal_account)):
        shares_on()
        with loop.adata(acc) as d:
            s = my_share(d, acc, sid)
            if s.status != "pending":
                raise HTTPException(409, "Already decided")
            token = secrets.token_urlsafe(32)
            s.token_hash, s.status, s.decided_at = share_hash(token), "approved", now()
            s.decided_by_account, s.expires_at = acc.id, now() + timedelta(days=s.expires_in_days)
            d.commit()
            tenant, title, expires = s.tenant_id, s.title, s.expires_at
        url = f"{base_url(request)}/s/{token}"
        mailed = False
        if body.recipient_email:
            mailed = send_mail(app, str(body.recipient_email), "A report has been shared with you",
                               f"\"{title}\" has been shared with you with the athlete's approval. Open it here until "
                               f"{expires:%d %b %Y}: {url}\nPlease do not forward it.")
        return loop.wrap(tenant, {"status": "approved", "url": url, "expires_at": expires, "mailed": mailed,
                                  "notice": "Copy the link now: it is shown only once. You can withdraw it at any time."})

    def decide(sid, acc, from_states, to_state):
        shares_on()
        with loop.adata(acc) as d:
            s = my_share(d, acc, sid)
            if s.status not in from_states:
                raise HTTPException(409, "Not possible now")
            s.status, s.decided_at = to_state, now()
            s.decided_by_account = acc.id
            d.commit()
            return loop.wrap(s.tenant_id, share_json(s))

    @app.post("/api/v2/portal/report-shares/{sid}/decline")
    def portal_decline_share(sid: int, acc=Depends(portal_account)):
        return decide(sid, acc, ("pending",), "declined")

    @app.post("/api/v2/portal/report-shares/{sid}/withdraw")
    def portal_withdraw_share(sid: int, acc=Depends(portal_account)):
        return decide(sid, acc, ("approved",), "withdrawn")

    def open_share(token: str):
        if not 20 <= len(token) <= 80:
            return None
        with ident.Auth() as a:
            s = a.scalar(select(LoopShareRequest).where(LoopShareRequest.token_hash == share_hash(token)))
            if not s:
                return None
            l_ = a.get(LoopLink, s.link_id)
            outcome = ("withdrawn" if s.status in ("withdrawn", "declined") else "revoked" if s.status == "revoked"
                       else "ended" if not l_ or l_.status == "ended" else "expired" if _aware(s.expires_at) < now()
                       else "shown" if s.status == "approved" else "withdrawn")
            a.add(LoopShareView(share_id=s.id, outcome=outcome))
            a.commit()
            return json.loads(s.snapshot) if outcome == "shown" else None

    @app.get("/api/v2/shared-reports/{token}")
    def shared_json(token: str, request: Request):
        shares_on()
        guards.limit_ip(request, "report-link")
        snap = open_share(token)
        if not snap:
            raise HTTPException(404, "Not found")
        return snap

    @app.get("/s/{token}")
    def shared_page(token: str, request: Request):
        shares_on()
        guards.limit_ip(request, "report-link")
        from .reports import _page, snapshot_html
        snap = open_share(token)
        if not snap:
            return HTMLResponse(_page("<h1>Not available</h1><p>This link has expired, was withdrawn, or does not "
                                      "exist. Ask the athlete or the practitioner for a new one.</p>"), status_code=404,
                                headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})
        return HTMLResponse(_page(snapshot_html(snap)), headers={"Cache-Control": "no-store",
                                                                 "X-Robots-Tag": "noindex, nofollow"})

