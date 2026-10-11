from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import Request, Response
from pydantic import BaseModel


def http_date(epoch_seconds: float) -> str:
    return format_datetime(datetime.fromtimestamp(int(epoch_seconds), tz=UTC), usegmt=True)


def client_seen_at_or_after(if_modified_since: str | None, mtime_epoch: float) -> bool:
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
    """Pass the mtime AND every query value that changes the body: an unpassed part goes stale."""
    digest = hashlib.sha256("\x1f".join(repr(p) for p in parts).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


def client_has_etag(if_none_match: str | None, etag: str) -> bool:
    """Tolerates a proxy having stripped ``W/``: weak comparison ignores it."""
    if not if_none_match:
        return False
    wanted = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False


def model_json(model: BaseModel, *, headers: dict[str, str] | None = None) -> Response:
    """Serialized ONCE: returned as a model, FastAPI dumps, validates and encodes it again."""
    return Response(
        content=model.model_dump_json(by_alias=True),
        media_type="application/json",
        headers=headers,
    )


def conditional_json(request: Request, model: BaseModel, *, stamp: str | None = None) -> Response:
    """``stamp`` names a field that moves on every request: it stays out of the validator."""
    response = model_json(model)
    validated = (
        model.model_dump_json(by_alias=True, exclude={stamp}).encode() if stamp else response.body
    )
    etag = f'W/"{hashlib.sha256(validated).hexdigest()[:32]}"'
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return response
