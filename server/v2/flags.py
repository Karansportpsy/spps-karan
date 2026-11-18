"""Feature flags for the v2 stack. Every client-facing surface defaults to OFF.

The release preflight (server/release_preflight.py) refuses to start production with a flag on whose migration has
not been run. Flags are read on every call so tests can switch them with monkeypatch.
"""
from __future__ import annotations

import os

# name -> (default, description). The defaults here are the only defaults; DEPLOYMENT.md lists them.
FLAGS = {
    "SPPS_PORTAL_ENABLED": ("false", "v1 athlete/guardian portal (identity schema)"),
    "SPPS_ATHLETE_LOOP_ENABLED": ("false", "Phase 6 practitioner-invited athlete loop"),
    "SPPS_DIRECTORY_ENABLED": ("false", "Phase 7 public Find a Sports Psychologist directory"),
    "SPPS_SELF_SERVE_ENABLED": ("false", "Phase 8 self-serve athlete and guardian accounts"),
    "SPPS_AI_FEATURES_ENABLED": ("false", "Global AI switch; per-practitioner opt-in is also required"),
    "SPPS_REPORT_LINKS_ENABLED": ("false", "Phase 5 expiring external report links"),
    "SPPS_PRACTITIONER_CLOSURE_ENABLED": ("false", "Phase 2 orderly practitioner account closure"),
    "SPPS_DIRECTORY_BOOKING_ENABLED": ("false", "Booking-only directory: request an appointment from a directory card"),
}


def _on(name: str) -> bool:
    return os.getenv(name, FLAGS[name][0]).strip().lower() == "true"


def portal_enabled() -> bool:
    return _on("SPPS_PORTAL_ENABLED")


def athlete_loop_enabled() -> bool:
    # The athlete loop lives on the portal's identity accounts, so it needs both switches.
    return _on("SPPS_ATHLETE_LOOP_ENABLED") and portal_enabled()


def directory_enabled() -> bool:
    return _on("SPPS_DIRECTORY_ENABLED")


def self_serve_enabled() -> bool:
    # Self-serve accounts link through the athlete loop and are found through the directory.
    return _on("SPPS_SELF_SERVE_ENABLED") and athlete_loop_enabled() and directory_enabled()


def directory_booking_enabled() -> bool:
    # Requests are made from a portal account (13A identity) for a practitioner found in the directory.
    return _on("SPPS_DIRECTORY_BOOKING_ENABLED") and portal_enabled() and directory_enabled()


def ai_globally_enabled() -> bool:
    return _on("SPPS_AI_FEATURES_ENABLED")


def report_links_enabled() -> bool:
    return _on("SPPS_REPORT_LINKS_ENABLED")


def closure_enabled() -> bool:
    return _on("SPPS_PRACTITIONER_CLOSURE_ENABLED")


def snapshot() -> dict:
    return {name: _on(name) for name in FLAGS}
