"""Landing page logo contract (source-level; the page is rendered by the browser script).

The landing page shows the SPPS logo twice: in the site header and in the hero card on the left. The practitioner
login card on the right does not repeat it.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


def _login_card() -> str:
    start = APP.index('<div class="v1-auth-box">')
    end = APP.index('<div class="v1-auth-links">', start)
    return APP[start:end]


def test_login_card_has_no_logo_image():
    card = _login_card()
    assert "<img" not in card
    assert "v1-access-logo" not in card
    assert "<h2>Practitioner portal</h2>" in card


def test_header_and_hero_logos_are_kept():
    assert "v1-hero-logo" in APP
    assert 'class="brand-v1"' in APP[APP.index("function brand("):][:400]
    header = APP[APP.index("function header("):]
    assert "${brand()}" in header[:3000]


def test_landing_page_renders_the_logo_image_once_in_the_hero():
    hero = APP[APP.index('<div class="v1-hero-brand">'):APP.index('<div class="v1-kicker">')]
    assert hero.count("<img") == 1
    page = APP[APP.index('<section class="v1-hero"'):APP.index('id="practitioner-directory"')]
    assert len(re.findall(r"<img", page)) == 1


def test_unused_access_logo_style_is_gone():
    assert "v1-access-logo" not in STYLES


def test_staged_copies_match_the_source():
    for name in ("app.js", "styles.css"):
        source = (ROOT / "web" / name).read_bytes()
        assert (ROOT / "www" / "assets" / name).read_bytes() == source
        # The Android copy is generated build output (android/.gitignore ignores it), so a fresh checkout such as
        # CI does not have it. It is compared when present; the Web parity job runs `npm run android:sync` first
        # and then tests_android checks the native copy.
        native = ROOT / "android/app/src/main/assets/public/assets" / name
        if native.exists():
            assert native.read_bytes() == source, f"{native} is not regenerated from web/"
