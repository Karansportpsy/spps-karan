"""SPPS v2 production additions.

Everything new in the v2 stack lives in this package so the proven v1 code stays recognisable.
Rules that every module here follows:
  * Clinical-schema tables are declared on ``V2Base`` (server/v2/models.py), never on the v1 ``Base`` (which stays
    at exactly 33 tables), and never hold identity data for athletes or guardians.
  * Athlete/guardian identity tables are declared on ``V2IdentityBase`` (server/v2/identity_models.py) in
    ``spps_identity_dev``. Portal code reads clinical data only through narrow projection tables, never the
    clinical base tables.
  * Every route is added to the default-deny allowlist in server/v2/allowlist.py; nothing is reachable by default.
  * New client-facing surfaces are behind a feature flag that defaults to off (server/v2/flags.py).
"""
from __future__ import annotations


def install(app, ctx) -> None:
    """Register every v2 route group. ``ctx`` carries the shared dependencies from create_app."""
    from . import registration
    registration.register(app, ctx)
    from . import trust
    trust.register(app, ctx)
    from . import record
    record.register(app, ctx)
    from . import assessments
    assessments.register(app, ctx)
    from . import reports
    reports.register(app, ctx)
    from . import services
    services.register(app, ctx)
    from . import flags
    if flags.athlete_loop_enabled():
        from . import athlete_loop
        athlete_loop.register(app, ctx)
    if flags.directory_enabled():
        from . import directory
        directory.register(app, ctx)
    if flags.self_serve_enabled():
        from . import self_serve
        self_serve.register(app, ctx)
    if flags.directory_booking_enabled():
        from . import directory_booking
        directory_booking.register(app, ctx)
    if flags.guest_booking_enabled():
        from . import guest_booking
        guest_booking.register(app, ctx)
    if flags.push_enabled():
        from . import push
        push.register(app, ctx)
    if flags.notify_worker_enabled() or flags.push_enabled():
        # The delivery loop starts with the server and stops with it (server/notify_runner.py). It is wrapped around the
        # router lifespan, which works on every Starlette version; test clients that do not run the lifespan never
        # start it.
        from contextlib import asynccontextmanager
        inner = app.router.lifespan_context

        @asynccontextmanager
        async def _lifespan(a):
            async with inner(a) as state:
                from .. import notify_runner
                if getattr(a.state, "portal_auth_engine", None) is not None:
                    a.state.notify_thread = notify_runner.start(a, push_on=flags.push_enabled())
                try:
                    yield state
                finally:
                    stop = getattr(a.state, "notify_stop", None)
                    if stop is not None:
                        stop.set()
        app.router.lifespan_context = _lifespan
    # Later phases append their route groups here.
