"""The single owner of conditional-GET, in two flavours: ``If-Modified-Since`` when the body is one file,
``If-None-Match`` when it also depends on query values — a time validator cannot express a lens mask, an ETag can."""

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
    """A weak ETag over everything the body depends on — weak because the JSON is assembled per request. Callers pass the mtime
    AND every query value that changes the body; a part nobody passes can go stale silently."""
    digest = hashlib.sha256("\x1f".join(repr(p) for p in parts).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


def client_has_etag(if_none_match: str | None, etag: str) -> bool:
    """True iff the client's ``If-None-Match`` already holds *etag*. Handles the list form and tolerates a proxy having
    stripped ``W/`` (weak comparison ignores it). Malformed or absent → False, and the full body is served."""
    if not if_none_match:
        return False
    wanted = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False
