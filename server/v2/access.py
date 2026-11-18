"""Record-access audit: every read and write of an athlete record stores who, what and when (never content).

Route code calls ``note(user, athlete_id)`` after the ownership check (``athlete_for`` in server/main.py does this
for every v1 and v2 athlete, case, note, observation, report and timeline route). The entries are kept in a
context variable for the request and written by the request middleware after the response is ready, in their own
transaction on a separate one-connection pool (``app.state.AuditDB``) so an audit write never waits for the
connection its own request still holds, as the same practitioner (so the row-level policy ``actor = practitioner = current identity`` applies on
PostgreSQL). Only successful requests are recorded. One row per athlete per request.
"""
from __future__ import annotations

import contextvars
import logging

from .models import RecordAccess
from .reqlog import REQUEST_ID

_PENDING: contextvars.ContextVar[list | None] = contextvars.ContextVar("spps_access_pending", default=None)
LOG = logging.getLogger("spps.access")


def begin() -> contextvars.Token:
    return _PENDING.set([])


def end(token) -> None:
    _PENDING.reset(token)


def note(user, athlete_id: int | None, action: str | None = None) -> None:
    pending = _PENDING.get()
    if pending is None:
        return
    key = (user.id, athlete_id, action)
    if any(k == key for k, _ in pending):
        return
    pending.append((key, (user.id, user.tenant_id, athlete_id, action)))


def flush(app, method: str, route_path: str, status: int) -> None:
    pending = _PENDING.get()
    if not pending or status >= 400:
        return
    default_action = "read" if method in ("GET", "HEAD") else "write"
    rows_by_user: dict[tuple[int, int], list] = {}
    for _, (uid, tid, athlete_id, action) in pending:
        rows_by_user.setdefault((uid, tid), []).append((athlete_id, action or default_action))
    for (uid, tid), rows in rows_by_user.items():
        try:
            with getattr(app.state, "AuditDB", app.state.DB)() as db:
                if getattr(app.state, "v2_rls", False):
                    from ..identity import apply_identity, bind_identity

                    class _Who:
                        id, tenant_id = uid, tid
                    bind_identity(db, _Who)
                    # The audit pool has no after_begin hook of its own: set the identity on this transaction.
                    apply_identity(db.connection(), db.info["identity"])
                for athlete_id, action in rows:
                    db.add(RecordAccess(tenant_id=tid, practitioner_id=uid, actor_id=uid, athlete_id=athlete_id,
                                        action=action, resource=route_path[:120], request_id=REQUEST_ID.get()))
                db.commit()
        except Exception:  # the response is already decided; a lost audit row is logged loudly, never silently
            LOG.exception("record access audit write failed")
