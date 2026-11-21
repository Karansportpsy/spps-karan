"""Phase 9 — video consultation for CONFIRMED platform bookings.

Participants obtain tickets from their own authenticated sessions (no forwarded bearer
links). Every status/ICE/consent request and the WebSocket join re-validate the ticket
in the platform database (migration 0012). Two-peer signaling only: no media, recording
or facial analysis touches the server. Single-process peer registry; horizontal scaling
needs a shared registry (documented in PRODUCTION_READINESS).
"""
from __future__ import annotations

import json
import os
import secrets
from collections import defaultdict

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .ice_config import IceConfigError, ice_config
from .portal_auth import sha256_hex

ERRORS = {"28000": (401, "Session expired"), "P0002": (404, "Not found"),
          "P0004": (409, "Join video is available from 10 minutes before a confirmed online appointment until 15 minutes after its end"),
          "P0005": (409, "This video consultation has ended"),
          "22023": (422, "Invalid details")}


class RoomConsentIn(BaseModel):
    accepted: bool


def register_video_routes(app: FastAPI, *, mode: str, get_current) -> None:
    engine = app.state.portal_auth_engine
    current_session = app.state.portal_current_session
    peers: dict[int, dict[str, WebSocket]] = defaultdict(dict)

    def call(name, **params):
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine.begin() as conn:
                return conn.execute(text(f"SELECT * FROM spps_identity_dev.{name}({placeholders})"), params).fetchall()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", "")
            if code in ERRORS:
                raise HTTPException(*ERRORS[code]) from None
            raise

    def check(booking_id: int, ticket: str):
        rows = call("video_ticket_check", p_ticket_hash=sha256_hex(ticket or "-"))
        if not rows or rows[0].booking_id != booking_id:
            raise HTTPException(403, "Invalid, expired or revoked video ticket")
        return rows[0]

    def issue(fn, **params):
        ticket = secrets.token_urlsafe(32)
        # Positional SQL call: the ticket hash is always the LAST argument.
        expires = call(fn, **params, p_ticket_hash=sha256_hex(ticket))[0][0]
        return ticket, expires

    @app.post("/api/portal/bookings/{booking_id}/video-ticket")
    def portal_ticket(booking_id: int, session=Depends(current_session)):
        ticket, expires = issue("portal_video_ticket", p_token_hash=sha256_hex(session["token"]), p_booking_id=booking_id)
        return {"room_url": f"/video/{booking_id}?ticket={ticket}", "expires_at": expires.isoformat()}

    @app.post("/api/booking-requests/{booking_id}/video-ticket")
    def practitioner_ticket(booking_id: int, user=Depends(get_current)):
        ticket, expires = issue("practitioner_video_ticket", p_practitioner_id=user.id, p_booking_id=booking_id)
        return {"room_url": f"/video/{booking_id}?ticket={ticket}", "expires_at": expires.isoformat()}

    @app.post("/api/booking-requests/{booking_id}/end-video")
    async def end_video(booking_id: int, user=Depends(get_current)):
        revoked = call("practitioner_end_video", p_practitioner_id=user.id, p_booking_id=booking_id)[0][0]
        for peer in tuple(peers.get(booking_id, {}).values()):
            try:
                await peer.close(code=1000)
            except (RuntimeError, OSError):
                pass
        return {"ended": True, "tickets_revoked": revoked}

    @app.get("/api/video/{booking_id}/status")
    def status(booking_id: int, ticket: str = ""):
        t = check(booking_id, ticket)
        return {"room_id": booking_id, "role": t.role, "consented": t.consented,
                "recording_enabled": False, "facial_emotion_analysis_enabled": False}

    @app.get("/api/video/{booking_id}/ice")
    def ice(booking_id: int, ticket: str = ""):
        check(booking_id, ticket)
        try:
            # Opaque per-request label: TURN server logs cannot be linked to a booking.
            return ice_config("spps-" + secrets.token_hex(6), mode=mode)
        except IceConfigError:
            raise HTTPException(503, "Video relay is not configured correctly") from None

    @app.post("/api/video/{booking_id}/consent")
    def consent(booking_id: int, body: RoomConsentIn, ticket: str = ""):
        check(booking_id, ticket)
        if not body.accepted:
            raise HTTPException(422, "Explicit live video consent required")
        if not call("video_ticket_consent", p_ticket_hash=sha256_hex(ticket))[0][0]:
            raise HTTPException(403, "Invalid, expired or revoked video ticket")
        return {"consented": True, "recording_enabled": False}

    @app.websocket("/ws/video/{booking_id}")
    async def signal(websocket: WebSocket, booking_id: int):
        if mode == "staging" and os.getenv("MODEL_C_HOSTED_PILOT_UNLOCKED", "false").lower() != "true":
            await websocket.close(code=1008)
            return
        try:
            t = check(booking_id, websocket.query_params.get("ticket", ""))
            if not t.consented:
                raise HTTPException(403, "Consent required")
        except HTTPException:
            await websocket.close(code=1008)
            return
        role = t.role
        if role in peers[booking_id]:  # reserve before accept(): no concurrent role takeover
            await websocket.close(code=1008)
            return
        peers[booking_id][role] = websocket
        other_role = "guest" if role == "practitioner" else "practitioner"
        try:
            await websocket.accept()
            await websocket.send_json({"type": "ready", "role": role})
            other = peers[booking_id].get(other_role)
            if other:
                await websocket.send_json({"type": "peer-ready"})
                await other.send_json({"type": "peer-ready"})
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict) or message.get("type") not in ("offer", "answer", "ice"):
                    continue
                if len(json.dumps(message)) > 35000:
                    continue
                other = peers[booking_id].get(other_role)
                if other:
                    await other.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if peers[booking_id].get(role) is websocket:
                peers[booking_id].pop(role, None)
                other = peers[booking_id].get(other_role)
                if other:
                    try:
                        await other.send_json({"type": "peer-left"})
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        pass
            if not peers[booking_id]:
                peers.pop(booking_id, None)
