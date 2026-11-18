"""Structured request logging: one request id on every response and in every log line.

The id comes from a valid incoming ``X-Request-ID`` (8-64 of ``[A-Za-z0-9-]``) or is generated. It is held in a
context variable for the duration of the request, and a logging filter stamps it on every record emitted by any
logger while the request runs, so application warnings, SQL errors and mail failures all carry it.

Personal data never enters a log line: the access log records method, path (no query string), status and timing;
path segments that look like tokens are masked. ``redact`` is applied to every message as a last line of defence
and masks e-mail addresses and long bearer-like strings.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("spps_request_id", default="-")
_RID = re.compile(r"[A-Za-z0-9-]{8,64}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{32,}(?:\.[A-Za-z0-9_\-]+){0,2}")
ACCESS = logging.getLogger("spps.request")


def redact(text: str) -> str:
    return _TOKENISH.sub("[redacted]", _EMAIL.sub("[email]", text))


def safe_path(path: str) -> str:
    # Token-bearing paths (invite, report links) are masked segment by segment.
    return "/".join(redact(seg) for seg in path[:200].split("/"))


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


_installed = False


def install_logging() -> None:
    """Attach the filter to the root handlers once; idempotent across create_app calls in tests."""
    global _installed
    if _installed:
        return
    flt = RequestIdFilter()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s"))
        root.addHandler(handler)
        if root.level == logging.WARNING:
            root.setLevel(logging.INFO)
    for handler in root.handlers:
        handler.addFilter(flt)
    logging.getLogger("spps").addFilter(flt)
    _installed = True


def incoming_id(header: str | None) -> str:
    return header if header and _RID.fullmatch(header) else uuid.uuid4().hex


def access_line(rid: str, method: str, path: str, status: int, started: float) -> str:
    return json.dumps({"event": "http", "rid": rid, "method": method, "path": safe_path(path),
                       "status": status, "ms": round((time.monotonic() - started) * 1000, 1)})
