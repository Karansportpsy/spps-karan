"""SPPS-VIDEO-001: ICE configuration, short-lived TURN credentials, relay policy."""
import base64
import hashlib
import hmac

import pytest

from server.ice_config import IceConfigError, ice_config, turn_credential

SECRET = "fictional-test-secret-" + "x" * 20  # test-only, never a deployed value
NOW = 1_800_000_000


def test_default_is_host_only_and_shares_nothing():
    assert ice_config("l", env={}) == {"iceServers": [], "iceTransportPolicy": "all"}


def test_multiple_urls_are_grouped_and_deduplicated():
    env = {"MODEL_C_ICE_URLS": "stun:s.example.org:3478, turn:t.example.org:3478?transport=udp,"
                               "turns:t.example.org:443?transport=tcp",
           "MODEL_C_STUN_URL": "stun:s.example.org:3478", "MODEL_C_TURN_SECRET": SECRET}
    cfg = ice_config("lbl", env=env, now=NOW)
    assert cfg["iceServers"][0] == {"urls": ["stun:s.example.org:3478"]}
    turn = cfg["iceServers"][1]
    assert turn["urls"] == ["turn:t.example.org:3478?transport=udp", "turns:t.example.org:443?transport=tcp"]
    assert SECRET not in str(cfg)


def test_turn_rest_credential_matches_coturn_scheme_and_expires():
    env = {"MODEL_C_ICE_URLS": "turn:t.example.org:3478", "MODEL_C_TURN_SECRET": SECRET,
           "MODEL_C_TURN_TTL_SECONDS": "600"}
    cfg = ice_config("spps-abc", env=env, now=NOW)
    server = cfg["iceServers"][0]
    assert server["username"] == f"{NOW + 600}:spps-abc"
    expected = base64.b64encode(hmac.new(SECRET.encode(), server["username"].encode(), hashlib.sha1).digest()).decode()
    assert server["credential"] == expected
    assert cfg["credentialExpiresAt"] == NOW + 600


@pytest.mark.parametrize("ttl,expect", [("1", 300), ("999999", 7200)])
def test_ttl_is_clamped(ttl, expect):
    _, _, expires = turn_credential(SECRET, "l", 0, now=NOW)
    assert expires == NOW
    env = {"MODEL_C_ICE_URLS": "turn:t.example.org:3478", "MODEL_C_TURN_SECRET": SECRET, "MODEL_C_TURN_TTL_SECONDS": ttl}
    assert ice_config("l", env=env, now=NOW)["credentialExpiresAt"] == NOW + expect


def test_relay_policy_requires_turn_and_is_passed_through():
    with pytest.raises(IceConfigError):
        ice_config("l", env={"MODEL_C_ICE_TRANSPORT_POLICY": "relay"})
    with pytest.raises(IceConfigError):
        ice_config("l", env={"MODEL_C_ICE_TRANSPORT_POLICY": "bogus"})
    env = {"MODEL_C_ICE_URLS": "turn:127.0.0.1:3478?transport=tcp", "MODEL_C_TURN_SECRET": SECRET,
           "MODEL_C_ICE_TRANSPORT_POLICY": "relay"}
    assert ice_config("l", env=env, now=NOW)["iceTransportPolicy"] == "relay"


@pytest.mark.parametrize("env", [
    {"MODEL_C_ICE_URLS": "http://evil.example.org"},
    {"MODEL_C_ICE_URLS": "turn:t.example.org:3478", "MODEL_C_TURN_SECRET": "short"},
    {"MODEL_C_ICE_URLS": "turn:t.example.org:3478"},  # no credential at all
])
def test_misconfiguration_fails_closed(env):
    with pytest.raises(IceConfigError):
        ice_config("l", env=env)


def test_static_credentials_only_outside_hosted_modes():
    env = {"MODEL_C_TURN_URL": "turn:t.example.org:3478", "MODEL_C_TURN_USERNAME": "u", "MODEL_C_TURN_CREDENTIAL": "c"}
    assert ice_config("l", mode="local", env=env)["iceServers"][0]["username"] == "u"
    for mode in ("staging", "production"):
        with pytest.raises(IceConfigError):
            ice_config("l", mode=mode, env=env)
