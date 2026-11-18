"""Free hosted routes may expose only public profiles/inquiries, never clinical records."""
from server import development_profile as p


def test_hosted_free_public_route_gates(monkeypatch):
    monkeypatch.setenv('SPPS_HOSTED_RLS','true')
    monkeypatch.setenv('SPPS_BETA_V1_FREE','true')
    for method,url in (
        ('GET','/api/public/services'),
        ('GET','/api/public/practitioners'),
        ('GET','/api/public/practitioners/12/photo'),
        ('GET','/api/public/practitioners/12/slots'),
        ('POST','/api/public/enquiries'),
        ('POST','/api/public/practitioner-registration'),
    ):
        assert p.allowed(method,url), (method,url)
    for method,url in (
        ('POST','/api/public/bookings'),
        ('POST','/api/public/practitioner-registration/checkout'),
        ('GET','/api/public/practitioners/12/email'),
        ('GET','/api/public/athletes'),
        ('GET','/api/athletes/20/clinical-history'),
    ):
        assert not p.allowed(method,url), (method,url)


def test_off_mode_keeps_hosted_free_public_routes_closed(monkeypatch):
    monkeypatch.setenv('SPPS_HOSTED_RLS','true')
    monkeypatch.setenv('SPPS_BETA_V1_FREE','false')
    assert not p.allowed('POST','/api/public/practitioner-registration')
    assert not p.allowed('GET','/api/public/practitioners')
