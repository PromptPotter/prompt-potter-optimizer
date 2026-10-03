"""The single owner of conditional-GET, in two flavours: ``If-Modified-Since`` when the body is one file,
``If-None-Match`` when it also depends on query values — a time validator cannot express a lens mask, an ETag can."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import Request, Response
from pydantic import BaseModel


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


def model_json(model: BaseModel, *, headers: dict[str, str] | None = None) -> Response:
    """A served model, serialized ONCE: returned as a model, FastAPI dumps it to Python objects,
    validates and encodes it again. ``response_model=`` stays on the decorator, for the spec."""
    return Response(
        content=model.model_dump_json(by_alias=True),
        media_type="application/json",
        headers=headers,
    )


def conditional_json(request: Request, model: BaseModel, *, stamp: str | None = None) -> Response:
    """``model_json`` under a validator cut from the body itself, so no caller can leave out a part
    it depends on. ``stamp`` names a field that moves on every request: it stays out of the cut."""
    response = model_json(model)
    validated = (
        model.model_dump_json(by_alias=True, exclude={stamp}).encode() if stamp else response.body
    )
    etag = f'W/"{hashlib.sha256(validated).hexdigest()[:32]}"'
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return response
