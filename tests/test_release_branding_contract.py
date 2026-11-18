"""Production release branding contract: all interfaces share one visible identity."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BRAND="SPPS Version 1.0 - Free"

def test_release_badge_is_shared():
    js=(ROOT/"web/app.js").read_text(encoding="utf-8")
    html=(ROOT/"web/index.html").read_text(encoding="utf-8")
    assert BRAND in html
    assert 'function header()' in js and 'class="spps-release-badge"' in js
    assert "function pageHead(" in js
    assert "function adminApprovalPage()" in js
    assert "function directoryPage()" in js
    assert "const betaLabel='"+BRAND+"'" in js
    assert "Fictional-data staging" not in js
    assert "Validation and review only" not in js

def test_bundled_web_matches_native():
    for web,apk in [("web/app.js","www/assets/app.js"),("web/index.html","www/index.html"),("web/styles.css","www/assets/styles.css")]:
        assert (ROOT/web).read_bytes()==(ROOT/apk).read_bytes()

def test_athlete_portal_and_video_room_share_release_badge():
    for path in ("web/portal.html","web/portal.js","web/room.html","web/room.js"):
        text=(ROOT/path).read_text(encoding="utf-8")
        assert BRAND in text, path
        assert "Beta Version 1.4.2" not in text and "Beta v1.4.2" not in text
    assert 'appName:\'SPPS Free 1.0\'' in (ROOT/"scripts/configure-android.mjs").read_text()

def test_legal_pages_are_final_and_carry_the_release_brand():
    for path in ("web/privacy.html","web/terms.html","web/policies.html","web/guide.html"):
        source=(ROOT/path).read_text(encoding="utf-8")
        assert BRAND in source
        for word in ("Not approved for production","REVIEW DRAFT","Review Draft","Beta","fictional","demo"):
            assert word not in source, (path, word)
        assert "{{CONTACT_LINK}}" in source or path=="web/guide.html" or "{{CONTACT_EMAIL}}" in source
