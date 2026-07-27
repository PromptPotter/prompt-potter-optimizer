"""Shared HTTP conditional-request helpers for the campaign read routes — the single
owner of conditional-GET, in two flavours: ``If-Modified-Since`` for a body that is one
file (the ``dashboard`` poll), ``If-None-Match`` for a body that also depends on query
values (the ``tree``/``ray`` reads — a time validator cannot express the
``lens``/``samples`` mask; an ETag folds it in, which is what makes a masked 304 possible).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime


def http_date(epoch_seconds: float) -> str:
    """Format an mtime as an HTTP-date (RFC 7231 §7.1.1.1). Second resolution."""
    return format_datetime(datetime.fromtimestamp(int(epoch_seconds), tz=UTC), usegmt=True)


def client_seen_at_or_after(if_modified_since: str | None, mtime_epoch: float) -> bool:
    """Return True iff the client's ``If-Modified-Since`` covers the current mtime.
    Malformed header → False (serve full body)."""
    if not if_modified_since:
        return False
    try:
        client_dt = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False
    if client_dt is None:
        return False
    return int(client_dt.timestamp()) >= int(mtime_epoch)


def weak_etag(*parts: object) -> str:
    """A weak ETag over *parts* — everything the response body depends on.

    Weak (``W/``) because the body is JSON assembled per request: two responses for the
    same parts are semantically identical but not guaranteed byte-identical, which is
    precisely what weak comparison means. Callers pass the mtime AND every query value
    that changes the body; a part nobody passes is a part that can go stale silently.

    ``None`` parts are included as such, so "no lens" and ``lens=""`` cannot collide.
    """
    digest = hashlib.sha256("\x1f".join(repr(p) for p in parts).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


def client_has_etag(if_none_match: str | None, etag: str) -> bool:
    """Return True iff the client's ``If-None-Match`` already holds *etag*.

    Handles the comma-separated list form and tolerates a proxy having stripped the
    ``W/`` prefix (weak comparison ignores it — RFC 7232 §2.3.2). ``*`` matches any
    current representation. Malformed/absent → False (serve the full body).
    """
    if not if_none_match:
        return False
    wanted = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False
