"""Phase 9: video consultation for CONFIRMED platform bookings (tickets + signaling)."""
import random
from contextlib import contextmanager

import pytest
from starlette.websockets import WebSocketDisconnect

from tests_integration.test_portal_auth import verified_login
from tests_integration.test_portal_bookings import admin, approve, bookable, future_slot, pay, request
from tests_integration.test_portal_links import practitioner


@contextmanager
def video_booking(app, env, *, in_window=True):
    alice, a, person, link = bookable(app, env, video=True)
    booking = request(a, link, future_slot(alice), video_requested=True).json()["booking_id"]
    approve(alice, booking)
    assert pay(a, booking)[0] == "CONFIRMED"
    if in_window:
        admin("UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '5 minutes', "
              "ends_at=now()+interval '55 minutes' WHERE id=:b", {"b": booking})
    try:
        yield alice, a, person, link, booking
    finally:  # leave the shared "now" window free for other tests
        hours = random.SystemRandom().randint(24, 175000)
        admin("UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-make_interval(hours=>:h), "
              "ends_at=now()-make_interval(hours=>:h)+interval '50 minutes' WHERE id=:b", {"b": booking, "h": hours})


def ticket_of(url):
    return url.split("ticket=", 1)[1]


def test_both_participants_get_tickets_consent_and_signal(app, integration_env):
    with video_booking(app, integration_env) as (alice, a, person, link, booking):
        p = alice.post(f"/api/booking-requests/{booking}/video-ticket").json()
        g = a.post(f"/api/portal/bookings/{booking}/video-ticket").json()
        pt, gt = ticket_of(p["room_url"]), ticket_of(g["room_url"])
        assert a.get(f"/api/video/{booking}/status?ticket={gt}").json()["role"] == "guest"
        assert a.get(f"/api/video/{booking}/status?ticket={pt}").json()["role"] == "practitioner"
        assert a.get(f"/api/video/{booking}/ice?ticket={gt}").json() == {"iceServers": [], "iceTransportPolicy": "all"}
        # WebSocket refused before explicit in-room consent.
        with pytest.raises(WebSocketDisconnect):
            with a.websocket_connect(f"/ws/video/{booking}?ticket={gt}") as ws:
                ws.receive_json()
        for t in (pt, gt):
            assert a.post(f"/api/video/{booking}/consent?ticket={t}", json={"accepted": True}).json()["consented"]
        with a.websocket_connect(f"/ws/video/{booking}?ticket={pt}") as doc, \
                a.websocket_connect(f"/ws/video/{booking}?ticket={gt}") as client:
            assert doc.receive_json() == {"type": "ready", "role": "practitioner"}
            assert client.receive_json() == {"type": "ready", "role": "guest"}
            assert client.receive_json() == {"type": "peer-ready"}
            assert doc.receive_json() == {"type": "peer-ready"}
            doc.send_json({"type": "offer", "sdp": "fictional-offer"})
            assert client.receive_json() == {"type": "offer", "sdp": "fictional-offer"}
            client.send_json({"type": "chat", "text": "dropped"})
            client.send_json({"type": "answer", "sdp": "fictional-answer"})
            assert doc.receive_json() == {"type": "answer", "sdp": "fictional-answer"}


def test_tickets_refused_outside_window_or_without_video(app, integration_env):
    with video_booking(app, integration_env, in_window=False) as (alice, a, person, link, booking):
        assert alice.post(f"/api/booking-requests/{booking}/video-ticket").status_code == 409
        assert a.post(f"/api/portal/bookings/{booking}/video-ticket").status_code == 409


def test_cancellation_link_revocation_and_consent_withdrawal_invalidate_tickets(app, integration_env):
    with video_booking(app, integration_env) as (alice, a, person, link, booking):
        gt = ticket_of(a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"])
        assert a.get(f"/api/video/{booking}/status?ticket={gt}").status_code == 200
        a.post("/api/portal/consents", json={"subject_person_id": person, "purpose": "VIDEO_CONSULTATION",
                                             "practitioner_id": alice.get("/api/auth/me").json()["id"], "decision": "WITHDRAWN"})
        assert a.get(f"/api/video/{booking}/status?ticket={gt}").status_code == 403
    with video_booking(app, integration_env) as (alice, a, person, link, booking):
        gt = ticket_of(a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"])
        a.post(f"/api/portal/links/{link}/revoke")
        assert a.get(f"/api/video/{booking}/status?ticket={gt}").status_code == 403
    with video_booking(app, integration_env) as (alice, a, person, link, booking):
        gt = ticket_of(a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"])
        alice.post(f"/api/booking-requests/{booking}/cancel", json={"reason": "Fictional practitioner illness"})
        assert a.get(f"/api/video/{booking}/status?ticket={gt}").status_code == 403


def test_reissue_end_session_and_outsiders(app, integration_env):
    with video_booking(app, integration_env) as (alice, a, person, link, booking):
        first = ticket_of(a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"])
        second = ticket_of(a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"])
        assert a.get(f"/api/video/{booking}/status?ticket={first}").status_code == 403  # superseded
        assert a.get(f"/api/video/{booking}/status?ticket={second}").status_code == 200
        bob = practitioner(app, integration_env, "bob")
        assert bob.post(f"/api/booking-requests/{booking}/video-ticket").status_code == 404
        other, _ = verified_login(app, integration_env)
        assert other.post(f"/api/portal/bookings/{booking}/video-ticket").status_code == 404
        assert a.get(f"/api/video/{booking + 1}/status?ticket={second}").status_code == 403  # ticket bound to booking
        assert a.get(f"/api/video/{booking}/status?ticket=forged-ticket-value").status_code == 403
        assert alice.post(f"/api/booking-requests/{booking}/end-video").json()["tickets_revoked"] >= 1
        assert a.get(f"/api/video/{booking}/status?ticket={second}").status_code == 403
        # Ending is final: neither participant can obtain a fresh ticket to reopen the consultation.
        for again in (alice.post(f"/api/booking-requests/{booking}/video-ticket"),
                      a.post(f"/api/portal/bookings/{booking}/video-ticket")):
            assert again.status_code == 409 and again.json()["detail"] == "This video consultation has ended"
        # ... and both booking lists say so, so neither client offers "Join video" again.
        assert [b["video_closed"] for b in alice.get("/api/booking-requests").json() if b["booking_id"] == booking] == [True]
        assert [b["video_closed"] for b in a.get("/api/portal/bookings").json() if b["booking_id"] == booking] == [True]
