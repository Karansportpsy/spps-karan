"""The application carries no fictional data, stock faces, demo seeding or payment simulation.

Fictional fixtures and provider stand-ins exist only in test_support/, which the application never imports and
the production image never copies (the Dockerfile copies server/, migrations/ and web/ only).
"""
import inspect
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.main as main
from server.models import Practitioner, PractitionerProfile, Tenant
from server.security import make_password

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_DIRS = ("server", "migrations", "web")
SHIPPED = [p for d in SHIPPED_DIRS for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
SIMULATION_ROUTES = ("/api/internal/test-capture/{bid}", "/api/internal/test-activate-subscription")


def test_dockerfile_copies_only_application_folders():
    copies = re.findall(r"(?m)^COPY\s+(\S+)", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert sorted(copies) == ["migrations/", "requirements-hosted.txt", "server/", "web/"]


def test_application_factory_has_no_seeding_and_never_imports_test_support():
    assert list(inspect.signature(main.create_app).parameters) == ["database_url", "init_database"]
    assert not (ROOT / "server" / "seed.py").exists() and not (ROOT / "server" / "local_demo").exists()
    for path in SHIPPED:
        if path.suffix == ".py":
            text = path.read_text(encoding="utf-8")
            assert "test_support" not in text and "local_demo" not in text, path.relative_to(ROOT)
            for name in ("seed_extended_demo", "upgrade_synthetic_demo", "ensure_demo_subscriptions",
                         "local_simulation", "local_test_capture", "mock_capture"):
                assert name not in text, f"{name} in {path.relative_to(ROOT)}"


def test_shipped_files_have_no_fictional_accounts_stock_faces_or_simulation_endpoints():
    for path in SHIPPED:
        assert not re.fullmatch(r"portrait_\d+\.webp", path.name), path
        if path.suffix in (".py", ".js", ".html", ".css"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in ("modelc.test", "DemoOnly", "DEMO_PRACTITIONERS", "/api/internal/test-",
                           "LOCAL DEMO", "Synthetic demo", "local demo"):
                assert marker not in text, f"{marker!r} in {path.relative_to(ROOT)}"
    www = ROOT / "www"
    if www.is_dir():
        assert not list(www.rglob("portrait_*.webp"))
        for path in www.rglob("*.js"):
            assert "/api/internal/test-" not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_application_alone_exposes_no_simulation_route_and_serves_no_stock_portrait(tmp_path):
    for name in SHIPPED_DIRS:
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    code = ("from server.main import create_app; from fastapi.testclient import TestClient; "
            "a=create_app('sqlite:///smoke.db',init_database=True); c=TestClient(a); "
            "paths={r.path for r in a.routes}; "
            f"assert not paths & set({SIMULATION_ROUTES!r}), paths; "
            "assert c.get('/health/live').status_code==200 and c.get('/').status_code==200; "
            "assert c.get('/assets/avatars/placeholder.svg').status_code==200; "
            "assert c.get('/assets/avatars/portrait_01.webp').status_code==404; "
            "assert c.post('/api/internal/test-activate-subscription',json={}).status_code in (404,405); print('ok')")
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and run.stdout.strip().endswith("ok"), run.stderr[-1500:]


def test_placeholder_is_a_neutral_self_contained_svg():
    svg = (ROOT / "web" / "avatars" / "placeholder.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and 'width="256"' in svg and 'height="256"' in svg
    assert "href" not in svg and "<image" not in svg and "<script" not in svg


def test_practitioner_without_a_photo_gets_the_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_C_PUBLIC_TENANT", "clean-tenant")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    app = main.create_app(f"sqlite:///{tmp_path / 'clean.db'}", init_database=True)
    client = TestClient(app)
    with app.state.DB() as db:
        tenant = Tenant(slug="clean-tenant", display_name="Clean Tenant")
        db.add(tenant); db.flush()
        person = Practitioner(tenant_id=tenant.id, email="listed@example.com", name="Dr Listed Person",
                              password_hash=make_password("Not-A-Real-Password-1"), active=True)
        db.add(person); db.flush()
        db.add(PractitionerProfile(practitioner_id=person.id, qualifications="MSc Sport Psychology", experience_years=4,
                                   achievements="x", public_approved=True, credentials_verified=True, status="approved"))
        db.commit()
    listed = client.get("/api/public/practitioners").json()
    assert [p["photo_url"] for p in listed] == ["/assets/avatars/placeholder.svg"]
    assert client.get("/assets/avatars/placeholder.svg").headers["content-type"].startswith("image/svg")


def test_retired_flags_and_a_missing_tenant_are_refused_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_C_ENV", "production")
    monkeypatch.setenv("MODEL_C_COOKIE_SECURE", "true")
    for value in ("", "model-c-demo"):
        monkeypatch.setenv("MODEL_C_PUBLIC_TENANT", value)
        with pytest.raises(RuntimeError, match="MODEL_C_PUBLIC_TENANT"):
            main.create_app(f"sqlite:///{tmp_path / 'p.db'}", init_database=False)
    monkeypatch.setenv("MODEL_C_PUBLIC_TENANT", "real-tenant")
    for flag in ("MODEL_C_SEED_DEMO", "MODEL_C_PAYMENT_TEST_MODE", "MODEL_C_ALLOW_TEST_SUBSCRIPTION_ACTIVATION"):
        monkeypatch.setenv(flag, "true")
        with pytest.raises(RuntimeError, match="refuses"):
            main.create_app(f"sqlite:///{tmp_path / 'p.db'}", init_database=False)
        monkeypatch.delenv(flag)
