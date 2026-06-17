"""Shared HTTP conditional-request helpers for the campaign read routes.

Both the per-cycle ``dashboard`` poll and the campaign ``lineage`` poll honor
``If-Modified-Since`` → ``304 Not Modified`` so the 2 s webapp polls stay cheap
during quiescent stretches — the body is only recomputed when an on-disk mtime
has actually advanced.
"""

from __future__ import annotations

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
