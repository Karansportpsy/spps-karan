"""Regression tests for local SPPS two-party signaling (fictional booking only)."""
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest
from test_support.app import create_app


def test_two_role_signaling_relay_and_reject_invalid_access(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_C_WEBRTC_ENABLED','true')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE','true')
    app = create_app(f"sqlite:///{tmp_path/'ws-test.db'}", init_database=True, seed_demo=True)
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={
            'email':'practitioner@modelc.test','password':'DemoOnly!2026','terms_accepted':True
        })
        assert login.status_code==200, login.text
        headers={'X-CSRF-Token':login.json()['csrf']}
        practitioner=next(p for p in client.get('/api/public/practitioners').json() if p['name'])
        # Existing seeded appointment or a newly scheduled fictional one.
        slots=client.get(f"/api/public/practitioners/{practitioner['id']}/slots").json()
        assert slots, 'No synthetic appointment slots seeded'
        booking=client.post('/api/public/bookings',json={
            'slot_id':slots[0]['id'],
            'contact_name':'Synthetic Video Guest','contact_email':'video-synthetic@example.org',
            'contact_consent':True,'video_consent':True
        })
        assert booking.status_code==201, booking.text
        booking_id=booking.json()['booking_id']
        capture=client.post(f'/api/internal/test-capture/{booking_id}',headers=headers)
        assert capture.status_code==200, capture.text
        approve=client.post(f'/api/bookings/{booking_id}/approve',headers=headers)
        assert approve.status_code==200, approve.text
        invitation=client.post(f'/api/bookings/{booking_id}/room-invite',headers=headers)
        assert invitation.status_code==200, invitation.text
        data=invitation.json(); rid=data['room_id']
        for role in ('practitioner','guest'):
            consent=client.post(f'/api/room/{rid}/consent?access={data[role+"_token"]}',json={'accepted':True})
            assert consent.status_code==200,consent.text
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f'/ws/room/{rid}?access=invalid-token'):
                pass
        assert exc.value.code==1008
        with client.websocket_connect(f'/ws/room/{rid}?access={data["practitioner_token"]}') as practitioner_ws:
            assert practitioner_ws.receive_json()['type']=='ready'
            with client.websocket_connect(f'/ws/room/{rid}?access={data["guest_token"]}') as guest_ws:
                assert guest_ws.receive_json()['type']=='ready'
                assert guest_ws.receive_json()['type']=='peer-ready'
                assert practitioner_ws.receive_json()['type']=='peer-ready'
                fake_offer={'type':'offer','sdp':{'type':'offer','sdp':'synthetic-sdp-not-a-real-call'}}
                practitioner_ws.send_json(fake_offer)
                assert guest_ws.receive_json()==fake_offer
                fake_answer={'type':'answer','sdp':{'type':'answer','sdp':'synthetic-answer'}}
                guest_ws.send_json(fake_answer)
                assert practitioner_ws.receive_json()==fake_answer
