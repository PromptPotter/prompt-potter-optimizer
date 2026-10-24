from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import Request, Response
from pydantic import BaseModel

# A restart may serve new code over unchanged inputs, so no ETag outlives its process.
_PROCESS = os.urandom(8).hex()


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


def _weak(digest: str) -> str:
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


def conditional_json(
    request: Request, version: tuple[object, ...], build: Callable[[], BaseModel]
) -> Response:
    """A 304 before *build* runs: an input or query value left out of *version* goes stale."""
    etag = _weak(hashlib.sha256("\x1f".join(map(repr, (_PROCESS, *version))).encode()).hexdigest())
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    return model_json(build(), headers={"ETag": etag})


def hashed_json(request: Request, model: BaseModel, *, stamp: str | None = None) -> Response:
    """Decided after the work, for a read no input stamp covers; ``stamp`` moves on every request."""
    response = model_json(model)
    validated = (
        model.model_dump_json(by_alias=True, exclude={stamp}).encode() if stamp else response.body
    )
    etag = _weak(hashlib.sha256(validated).hexdigest())
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return response
