"""Regression: registration photo upload must not rely on blob: images.

The production Content-Security-Policy allows img-src 'self' data: only, so loading
a blob: object URL into an Image() is blocked and surfaces as "Invalid image",
which prevents every new practitioner from registering (a photo is required).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _img_src_directives():
    text = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
    return re.findall(r"img-src([^;\"']*(?:'[^']*'[^;\"']*)*)", text)


def test_csp_img_src_does_not_allow_blob():
    directives = _img_src_directives()
    assert directives, "expected img-src directives in server/main.py"
    assert all("blob:" not in d for d in directives)


def test_profile_photo_helper_uses_data_urls_not_blob_urls():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function profilePhotoBase64")
    end = app_js.index("\n", start)
    helper = app_js[start:end]
    assert "createObjectURL" not in helper
    assert "readAsDataURL" in helper
