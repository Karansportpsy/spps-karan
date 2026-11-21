"""Default-deny allowlist for every v2 route on the restricted (RLS) profiles.

``server.development_profile.allowed`` asks ``v2_allowed`` first. It returns True for an allowlisted v2 route whose
feature is switched on, False for a v2 route whose feature is off (so a flag-off surface is never reachable, even
if a handler were registered), and None for anything that is not a v2 route (the v1 rules then decide).
Every phase adds its patterns here; tests/test_v2_allowlist.py checks that each registered v2 route is listed.
"""
from __future__ import annotations

import re

from . import flags

# (method set, pattern, flag check or None)
ROUTES: list[tuple[frozenset, re.Pattern, object]] = []


def add(methods: str, pattern: str, flag=None) -> None:
    ROUTES.append((frozenset(methods.split(",")), re.compile("^" + pattern + "$"), flag))


# Phase 1 — registration and operations
add("GET,HEAD", r"/api/public/form-token")
add("POST", r"/api/public/practitioner-applications/pending")
add("POST", r"/api/public/practitioner-application/(?:promote|photo)")
add("GET,HEAD", r"/api/admin/registrations/summary")
add("GET,HEAD", r"/api/admin/registrations/\d+/transitions")
add("POST", r"/api/admin/email-deliveries/\d+/resend")

# Phase 2 — trust foundation
add("GET,HEAD", r"/api/v2/athletes/\d+/access-log")
add("GET,HEAD", r"/api/v2/admin/access-counts")
add("POST", r"/api/v2/account/export")
add("GET,HEAD", r"/api/v2/account/exports")
add("GET,HEAD", r"/api/v2/settings")
add("POST", r"/api/v2/settings/ai", flags.ai_globally_enabled)
add("GET,HEAD", r"/api/v2/account/closure", flags.closure_enabled)
add("POST", r"/api/v2/account/closure/(?:start|dispositions|cancel|complete)", flags.closure_enabled)
add("GET,HEAD", r"/api/v2/admin/closures", flags.closure_enabled)
add("POST", r"/api/v2/admin/closures/\d+/(?:restore|purge)", flags.closure_enabled)

# Phase 3 — the practitioner record
add("GET,HEAD", r"/api/v2/athletes/\d+/(?:profile|formulation|timeline)")
add("PUT", r"/api/v2/athletes/\d+/profile")
add("POST", r"/api/v2/athletes/\d+/formulation")
add("GET,HEAD", r"/api/v2/note-templates")
add("POST", r"/api/v2/note-templates(?:/\d+/archive)?")
add("GET,HEAD", r"/api/v2/note-drafts")
add("PUT", r"/api/v2/note-drafts/[A-Za-z0-9-]{8,40}")
add("POST", r"/api/v2/note-drafts/[A-Za-z0-9-]{8,40}/(?:commit|discard)")
add("GET,HEAD", r"/api/v2/offline-key")
add("GET,HEAD", r"/api/v2/notes/\d+")
add("POST", r"/api/v2/notes/\d+/(?:sign|amendments)")
add("POST", r"/api/v2/imports(?:/\d+/(?:preview|commit|discard))?")

# Phase 4 — assessments over time
add("GET,HEAD", r"/api/v2/instruments(?:/\d+)?")
add("POST", r"/api/v2/instruments(?:/\d+/archive)?")
add("POST", r"/api/v2/admin/instruments/\d+/licence")
add("GET,HEAD", r"/api/v2/athletes/\d+/instrument-administrations")
add("POST", r"/api/v2/athletes/\d+/instrument-administrations")
add("GET,HEAD", r"/api/v2/athletes/\d+/instrument-trends/\d+")

# Phase 5 — reports for coaches and the wider team
add("GET,HEAD", r"/api/v2/report-templates")
add("POST", r"/api/v2/report-templates")
add("GET,HEAD", r"/api/v2/athletes/\d+/(?:v2-reports|report-consents)")
add("POST", r"/api/v2/athletes/\d+/(?:v2-reports|report-consents)")
add("GET,HEAD", r"/api/v2/v2-reports/\d+(?:/(?:preview|rendered|pdf|links))?")
add("PUT", r"/api/v2/v2-reports/\d+")
add("POST", r"/api/v2/v2-reports/\d+/sign")
add("POST", r"/api/v2/report-consents/\d+/withdraw")
add("POST", r"/api/v2/v2-reports/\d+/links", flags.report_links_enabled)
add("POST", r"/api/v2/report-links/\d+/revoke")
add("GET,HEAD", r"/api/v2/report-links/[A-Za-z0-9_-]{20,80}", flags.report_links_enabled)
add("GET,HEAD", r"/r/[A-Za-z0-9_-]{20,80}", flags.report_links_enabled)

# Phase 6 — practitioner-invited athlete loop (both SPPS_PORTAL_ENABLED and SPPS_ATHLETE_LOOP_ENABLED)
_loop = flags.athlete_loop_enabled
add("POST", r"/api/v2/loop/athletes/\d+/invite", _loop)
add("GET,HEAD", r"/api/v2/loop/links", _loop)
add("GET,HEAD", r"/api/v2/loop/links/\d+/activity", _loop)
add("POST", r"/api/v2/loop/links/\d+/(?:end|assignments|alert-rules|summaries|guardian-detail)", _loop)
add("PUT", r"/api/v2/loop/links/\d+/checkin-plan", _loop)
add("POST", r"/api/v2/loop/alerts/\d+/acknowledge", _loop)
add("GET,HEAD", r"/api/v2/admin/crisis-contacts", _loop)
add("PUT", r"/api/v2/admin/crisis-contacts", _loop)
add("POST", r"/api/v2/admin/loop/send-reminders", _loop)
add("GET,HEAD", r"/api/v2/portal/(?:crisis-contacts|me|links|export)", _loop)
add("GET,HEAD", r"/api/v2/portal/invites/[A-Za-z0-9_-]{20,80}", _loop)
add("POST", r"/api/v2/portal/invites/[A-Za-z0-9_-]{20,80}/accept", _loop)
add("POST", r"/api/v2/portal/auth/(?:register|verify|login|logout)", _loop)
add("POST", r"/api/v2/portal/links/\d+/(?:end|guardian-detail|checkins)", _loop)
add("GET,HEAD", r"/api/v2/portal/links/\d+/(?:assignments|checkins|guardian-view)", _loop)
add("POST", r"/api/v2/portal/assignments/\d+/submit", _loop)

# Phase 7 — Find a Sports Psychologist (SPPS_DIRECTORY_ENABLED)
_dir = flags.directory_enabled
add("GET,HEAD", r"/api/v2/directory/profile", _dir)
add("PUT", r"/api/v2/directory/profile", _dir)
add("POST", r"/api/v2/directory/profile/(?:submit|withdraw)", _dir)
add("GET,HEAD", r"/api/v2/admin/directory", _dir)
add("POST", r"/api/v2/admin/directory/\d+/(?:publish|delist)", _dir)
add("GET,HEAD", r"/api/v2/public/directory(?:/\d+(?:/photo)?)?", _dir)

# Booking-only directory, phase 2 — practitioner services (always on) and directory booking (flag)
add("GET,HEAD", r"/api/v2/practitioner-services")
add("POST", r"/api/v2/practitioner-services(?:/order|/\d+/(?:deactivate|activate))?")
add("PUT", r"/api/v2/practitioner-services/\d+")
_dbook = flags.directory_booking_enabled
add("GET,HEAD", r"/api/portal/directory/\d+/slots", _dbook)
add("POST", r"/api/portal/directory-bookings", _dbook)

# Guest booking (SPPS_GUEST_BOOKING_ENABLED, which needs directory booking)
_guest = flags.guest_booking_enabled
add("GET,HEAD", r"/api/v2/public/directory/\d+/slots", _guest)
add("POST", r"/api/v2/public/guest-bookings(?:/confirm)?", _guest)
add("POST", r"/api/v2/public/guest-claim", _guest)

# Phase 8 — self-serve athlete and guardian accounts (SPPS_SELF_SERVE_ENABLED, which needs loop + directory)
_self = flags.self_serve_enabled


def _self_links():
    return flags.self_serve_enabled() and flags.report_links_enabled()


add("GET,HEAD", r"/api/v2/portal/invitations", _self)
add("POST", r"/api/v2/portal/invitations/\d+/accept", _self)
add("POST", r"/api/v2/portal/account/(?:deactivate|delete)", _self)
add("POST", r"/api/v2/portal/auth/reactivate", _self)
add("GET,HEAD", r"/api/v2/v2-reports/\d+/share-requests", _self_links)
add("POST", r"/api/v2/v2-reports/\d+/share-requests", _self_links)
add("POST", r"/api/v2/report-shares/\d+/revoke", _self_links)
add("GET,HEAD", r"/api/v2/portal/report-shares", _self_links)
add("POST", r"/api/v2/portal/report-shares/\d+/(?:approve|decline|withdraw)", _self_links)
add("GET,HEAD", r"/api/v2/shared-reports/[A-Za-z0-9_-]{20,80}", _self_links)
add("GET,HEAD", r"/s/[A-Za-z0-9_-]{20,80}", _self_links)

# Phase 4 — notifications for every role (SPPS_PUSH_ENABLED, which needs the portal)
_push = flags.push_enabled
add("GET,HEAD", r"/api/(?:v2/push|portal/push)/state", _push)
add("POST", r"/api/(?:v2/push|portal/push)/(?:subscribe|unsubscribe|preferences|prompt)", _push)
add("GET,HEAD", r"/api/v2/push/config", _push)

# Prefixes that belong to v2 only: anything under them that is not listed above is refused.
V2_PREFIXES = ("/api/public/form-token", "/api/public/practitioner-applications/",
               "/api/public/practitioner-application/promote", "/api/public/practitioner-application/photo",
               "/api/admin/registrations", "/api/admin/email-deliveries", "/api/v2/", "/r/", "/s/")


def v2_allowed(method: str, path: str):
    for methods, pattern, flag in ROUTES:
        if method in methods and pattern.fullmatch(path):
            return bool(flag()) if flag else True
    if path.startswith(V2_PREFIXES):
        return False
    return None
