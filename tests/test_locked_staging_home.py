"""Locked hosted staging must never expose practitioner APIs or assets."""
import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from server import main as spps


def test_locked_staging_home_is_html_and_apis_remain_closed():
    environment = {
        "MODEL_C_ENV": "staging",
        "MODEL_C_HOSTED_PILOT_UNLOCKED": "false",
        "MODEL_C_COOKIE_SECURE": "true",
        "MODEL_C_ALLOWED_HOSTS": "testserver",
        "MODEL_C_SEED_DEMO": "false",
        "MODEL_C_INIT_DB": "false",
        "MODEL_C_PAYMENT_TEST_MODE": "false",
        "MODEL_C_ALLOW_TEST_ERASURE": "false",
        "MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION": "false",
    }
    # A no-data engine is enough: the status UI and rejected routes must never
    # query real athlete data. In production, the engine uses restricted Supabase.
    engine = create_engine("sqlite:///:memory:")
    with patch.dict(os.environ, environment), patch.object(spps, "make_engine", return_value=engine):
        app = spps.create_app(
            database_url="postgresql://placeholder:placeholder@example.invalid/postgres",
            init_database=False,
        )
        with TestClient(app) as client:
            home = client.get("/")
            assert home.status_code == 200
            assert "text/html" in home.headers["content-type"]
            assert "<!doctype html>" in home.text.lower()
            assert 'id="app"' in home.text
            assert "no-store" in home.headers["cache-control"]
            assert home.headers["x-robots-tag"].startswith("noindex")
            assert client.get("/favicon.ico").status_code == 204
            assert client.get("/health/live").json()["status"] == "ok"
            # Public marketing pages and static JS are intentionally reachable;
            # the lock still denies all clinical and authentication APIs.
            for public_url in ("/assets/app.js", "/privacy", "/terms", "/register"):
                public = client.get(public_url)
                assert public.status_code == 200, public_url
                assert public.headers["x-robots-tag"].startswith("noindex")
            for url in (
                "/studio",
                "/api/public/services",
                "/api/public/practitioner-plans",
                "/api/athletes",
            ):
                r = client.get(url)
                assert r.status_code == 503, url
                assert r.json()["status"] == "locked"
            r = client.post("/api/public/practitioner-registration", json={})
            assert r.status_code == 503
            assert r.json()["status"] == "locked"
