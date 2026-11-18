"""Legal pages and the user guide.

The pages are plain HTML in web/. Who operates SPPS and how to reach them come from server/owner_profile.py at the
moment the page is served, so the documents never carry a copy of the contact details that could go out of date.
"""
from __future__ import annotations

import html
from pathlib import Path

from fastapi.responses import HTMLResponse

from ..owner_profile import OWNER_EMAIL, OWNER_NAME

WEB = Path(__file__).resolve().parents[2] / "web"
PAGES = ("privacy.html", "terms.html", "policies.html", "guide.html")


def tokens() -> dict[str, str]:
    email = html.escape(OWNER_EMAIL)
    return {
        "{{OPERATOR_NAME}}": html.escape(OWNER_NAME),
        "{{CONTACT_EMAIL}}": email,
        "{{CONTACT_LINK}}": f'<a href="mailto:{email}">{email}</a>',
    }


def render_page(name: str) -> HTMLResponse:
    if name not in PAGES:
        raise ValueError(name)
    text = (WEB / name).read_text(encoding="utf-8")
    for key, value in tokens().items():
        text = text.replace(key, value)
    return HTMLResponse(text)
