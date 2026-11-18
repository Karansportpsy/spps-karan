"""SPPS Version 1.0 - Free: legal pages and guide are served complete, the UI carries no beta, demo or test-data
wording, and no simulated payment or test-data tooling ships in the application."""
import re
from pathlib import Path

from .conftest import fresh_client

ROOT = Path(__file__).resolve().parents[1]
UI = ["web/" + n for n in ("index.html", "app.js", "v2.js", "athlete.js", "athlete.html", "find.js", "find.html",
                           "portal.html", "portal.js", "room.html", "room.js", "your-data.html", "privacy.html",
                           "terms.html", "policies.html", "guide.html")]


def test_legal_pages_and_guide_are_served_with_the_operator_details(app):
    from server.owner_profile import OWNER_NAME
    c = fresh_client(app)
    for path in ("/terms", "/privacy", "/policies", "/guide"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "{{" not in r.text and "SPPS Version 1.0 - Free" in r.text, path
        assert OWNER_NAME in r.text or path == "/guide", path
        assert "mailto:" in r.text, path


def test_the_interface_carries_no_beta_demo_or_test_data_wording():
    banned = re.compile(r"Beta Version|BETA 1\.0|\bBeta v1|this beta|local beta|Fictional|[Dd]emo data|"
                        r"test data|test workspace|sandbox|REVIEW DRAFT|Review Draft|Not approved for production")
    for path in UI:
        text = (ROOT / path).read_text(encoding="utf-8")
        found = banned.findall(text)
        assert not found, (path, found)


def test_no_simulated_payment_or_test_data_tooling_ships():
    server = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "server").rglob("*.py"))
    for marker in ("SandboxProvider", "sandbox-checkout", "simulate_checkout", "/api/admin/purge", "/erase'",
                   "SPPS_V2_DEV_OUTBOX", "PURGE ALL TEST DATA"):
        assert marker not in server, marker
    web = (ROOT / "web" / "portal.js").read_text(encoding="utf-8") + (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "sandbox" not in web.lower() and "purge-workspace" not in web


def test_www_and_android_copies_include_the_new_pages():
    for name in ("policies.html", "guide.html", "terms.html", "privacy.html"):
        assert (ROOT / "web" / name).read_bytes() == (ROOT / "www" / name).read_bytes(), name


def test_release_metadata_names_the_same_release_everywhere():
    import json
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert pkg["version"] == "1.0.0-free" and lock["version"] == "1.0.0-free" and lock["packages"][""]["version"] == "1.0.0-free"
    assert pkg["name"] == lock["name"] == lock["packages"][""]["name"]
    assert "SPPS Version 1.0 - Free" in pkg["description"]
    for text in (pkg["name"], pkg["description"]):
        assert not re.search(r"(?i)beta|pilot|model.?c", text), text
    for doc in ("README.md", "PROGRESS.md", "DECISIONS.md", "START-HERE.md", "LAUNCH_CHECKLIST.md", "ANDROID_README.md",
                "DEPLOYMENT.md"):
        first = (ROOT / doc).read_text(encoding="utf-8").splitlines()[0]
        assert "SPPS Version 1.0 - Free" in first, (doc, first)
    gradle = (ROOT / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    assert 'versionName "1.0.0-free"' in gradle
